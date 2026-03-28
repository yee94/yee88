# Takopi Technical Architecture Analysis

## Executive Summary

Takopi is a sophisticated multi-agent orchestration platform that abstracts multiple AI code execution engines (Claude, OpenCode, Codex, Pi) through a unified subprocess protocol. The system provides a flexible event-driven architecture with extensive Telegram integration, cron-based scheduling, and context-aware execution management.

---

## Part 1: Runner Architecture (Engine Subprocess Protocol)

### 1.1 Core Runner Design Pattern

All runners extend from a base class hierarchy that provides a standardized JSON Line (JSONL) subprocess interface:

```
BaseRunner (session lock management)
    ↓
JsonlSubprocessRunner (JSONL parsing + event translation)
    ↓
Concrete Runners: ClaudeRunner, OpenCodeRunner, CodexRunner, PiRunner
```

**Key Principle**: Each runner is responsible for:
1. **Building CLI arguments** - translating prompts into engine-specific command lines
2. **Encoding stdin payloads** - optional input data for the subprocess
3. **Decoding JSONL streams** - parsing event lines using msgspec
4. **Event translation** - converting engine-specific events to unified `TakopiEvent` model
5. **Resume token extraction** - capturing session IDs from output

### 1.2 Claude Runner (`runners/claude.py`)

**Protocol**: JSONL via `--output-format stream-json`

**Subprocess Interface**:
```bash
claude -p --output-format stream-json --verbose [--resume TOKEN] 
  [--model MODEL] [--allowedTools TOOLS] [--dangerously-skip-permissions] -- PROMPT
```

**Key Design Points**:
- **Argument-based prompt delivery** (not stdin) - prompt appended after `--` separator
- **Tool input normalization** - supports nested content blocks with `.text` extraction
- **State tracking**: `ClaudeStreamState` maintains:
  - `pending_actions: dict[str, Action]` - tracks in-flight tool calls by ID
  - `last_assistant_text` - captures final response text
  - `note_seq` - counter for thinking block IDs

**Event Mapping**:
```
StreamSystemMessage(subtype="init")
  → StartedEvent (with session_id as resume token)

StreamToolUseBlock
  → ActionStartedEvent (kind inference from tool name/input)

StreamToolResultBlock (matches pending_actions by tool_use_id)
  → ActionCompletedEvent (with result preview + is_error flag)

StreamThinkingBlock
  → ActionCompletedEvent(kind="note") - thinking blocks as notes

StreamResultMessage (end of run)
  → CompletedEvent (with answer, resume token, usage data)
```

**Tool Kind Classification** (from `_tool_kind_and_title`):
- `bash`, `shell`, `killshell` → `kind="command"`
- `edit`, `write`, `multiedit` → `kind="file_change"` (extracts file_path)
- `read`, `glob`, `grep` → `kind="tool"` (file-based tools)
- `websearch`, `webfetch` → `kind="web_search"`
- `question`, `askuserquestion` → `kind="question"`
- Generic tools → `kind="subagent"`

**Configuration**:
```python
model: str | None                          # Override model
allowed_tools: list[str]                   # Default: ["Bash", "Read", "Edit", "Write"]
dangerously_skip_permissions: bool         # Skip permission checks
use_api_billing: bool                      # Use ANTHROPIC_API_KEY or local billing
```

**Error Handling**:
- Silently drops invalid JSON lines (logs as warning)
- Validates session_id presence before completion
- Falls back to `last_assistant_text` if result is empty

---

### 1.3 OpenCode Runner (`runners/opencode.py`)

**Protocol**: JSONL via `--format json`

**Subprocess Interface**:
```bash
opencode run --format json [--session SESSION_ID] [--model MODEL] -- PROMPT
```

**Key Design Points**:
- **Session ID format**: `ses_XXXX` (e.g., `ses_494719016ffe85dkDMj0FPRbHK`)
- **Numeric prompt workaround**: Prefixes pure numeric prompts with space to avoid parsing errors
- **State tracking**: `OpenCodeStreamState`:
  - `session_id` - lazily populated from first event
  - `emitted_started` - ensures only one StartedEvent
  - `saw_step_finish` - distinguishes graceful vs abnormal completion
  - `last_text` - accumulates text deltas

**Event Mapping**:
```
StepStart (with session_id)
  → StartedEvent (after emitting once)

ToolUse(part.state.status="completed"|"error"|None)
  → ActionStartedEvent (on None)
  → ActionCompletedEvent (on "completed" or "error", with exit_code check)
     - exit_code != 0 → is_error=True
     - Extracts output_preview (truncated to 500 chars)

Text(part.text)
  → TextDeltaEvent (accumulates to last_text)

StepFinish(part.reason="stop"|"tool-calls")
  → CompletedEvent(ok=True) [on "stop"]
  → TextFinishedEvent [on "tool-calls" with text reset for next step]

Error(error|message)
  → CompletedEvent(ok=False) [error string extracted from nested dict]
```

**Tool Action Extraction**:
- Looks for `callID` or `id` fields
- Extracts tool name from `part.tool`
- Merges input from `part.state.input`
- Supports file_change detection and question parsing

**File Transfer Integration**:
- Handles file metadata (path extraction, change tracking)
- Supports tool-specific input structure validation

---

### 1.4 Codex Runner (`runners/codex.py`)

**Protocol**: JSONL via `exec --json`

**Subprocess Interface**:
```bash
codex exec --json --skip-git-repo-check --color=never 
  [resume THREAD_ID -] -  [PROMPT via stdin]
```

**Key Design Points**:
- **stdin-based prompt delivery** - prompt encoded to bytes and sent via stdin
- **Resume via `resume THREAD_ID -` args** - special syntax for continuation
- **State tracking**: `CodexRunState`:
  - `factory: EventFactory(ENGINE)` - creates typed events
  - `final_answer` - captures last agent message
  - `turn_index` - tracks execution turns

**Event Mapping**:
```
ThreadStarted(thread_id)
  → StartedEvent (with thread_id as resume token)

ItemStarted/ItemUpdated/ItemCompleted(item)
  → Item-type-specific events via _translate_item_event

Item Types:
  - CommandExecutionItem → ActionEvent(kind="command")
    - Extracts command, exit_code, status
  - McpToolCallItem → ActionEvent(kind="tool")
    - Tracks server.tool hierarchy
    - Summarizes result (content_blocks count, structured content presence)
  - WebSearchItem → ActionEvent(kind="web_search")
  - FileChangeItem → ActionEvent(kind="file_change")
    - Normalizes change list format
  - TodoListItem → ActionEvent(kind="note")
    - Tracks done/total progress
  - ReasoningItem → ActionEvent(kind="note")

TurnStarted
  → ActionStartedEvent(kind="turn")

TurnCompleted(usage)
  → CompletedEvent(ok=True, usage=msgspec.to_builtins(usage))
```

**Reconnection Logic**:
- Regex: `^Reconnecting\.{3}\s*(?P<attempt>\d+)/(?P<max>\d+)\s*$`
- Emits action events with phase="started" (attempt 1) or "updated" (retries)

**Configuration**:
```python
extra_args: list[str]                      # Default: ["-c", "notify=[]"]
profile: str | None                        # Alternative profile selection
```

**Validation**:
- Prevents exec-only flags in extra_args (`--json`, `--output-schema`, etc.)
- These are managed by Takopi and cannot be overridden

---

### 1.5 Pi Runner (`runners/pi.py`)

**Protocol**: JSONL via `--mode json --print`

**Subprocess Interface**:
```bash
pi --print --mode json [--provider PROVIDER] 
  [--model MODEL] [--session SESSION_PATH] PROMPT
```

**Key Design Points**:
- **Session path format**: `~/.pi/agent/sessions/--SAFEPATH--/TIMESTAMP_TOKEN.jsonl`
  - Generates new session paths for fresh runs
  - Cwd converted to safe path: `/` → `-`, `:` → `-`
  - Supports environment override: `PI_CODING_AGENT_DIR`
- **Prompt sanitization**: Prepends space to prompts starting with `-` to avoid flag parsing
- **State tracking**: `PiStreamState`:
  - `resume: ResumeToken` - always initialized (with new session path or provided token)
  - `allow_id_promotion` - promotes path to short session ID on first message
  - `last_assistant_text`, `last_assistant_error`, `last_usage`

**Event Mapping**:
```
SessionHeader(id)
  → Potential session ID promotion (path → short_id)
  → StartedEvent (once per run)

ToolExecutionStart(toolCallId, toolName, args)
  → ActionStartedEvent (kind inference from tool name)
     - Supports file_change detection

ToolExecutionEnd(toolCallId, toolName, result, isError)
  → ActionCompletedEvent (with result + is_error)

MessageEnd(message with assistant role)
  → Captures text blocks + usage + error state
  → Updates state.last_* fields

AgentEnd(messages)
  → Extracts final assistant message
  → CompletedEvent(ok=!has_error, answer, error, usage)
```

**Session ID Promotion**:
- If initial token looks like path (contains `/`, `\`, `~`, or `.jsonl`)
- And `allow_id_promotion=True` (new run only)
- On first SessionHeader, promotes path to short_id format (8 chars before `-`)
- Disables promotion after first promotion to ensure consistency

**Configuration**:
```python
extra_args: list[str]                      # Additional CLI flags
model: str | None                          # Model override
provider: str | None                       # Provider selection (e.g., "openai")
```

**Resume Extraction**:
- Regex: `pi\s+--session\s+(.+?)`
- Supports quoted tokens with quote stripping

---

### 1.6 Shared Runner Features

#### ResumeTokenMixin
```python
class ResumeTokenMixin:
    engine: EngineId
    resume_re: re.Pattern[str]  # Engine-specific regex
    
    def is_resume_line(line: str) -> bool:
        # Check if line matches resume pattern
        
    def extract_resume(text: str | None) -> ResumeToken | None:
        # Find last matching resume token in text
```

#### SessionLockMixin
```python
class SessionLockMixin:
    session_locks: WeakValueDictionary[str, anyio.Semaphore]
    
    def lock_for(token: ResumeToken) -> anyio.Semaphore:
        # Returns per-session lock to prevent concurrent access
        # Uses WeakValueDictionary for automatic cleanup
        
    async def run_with_resume_lock(...):
        # Acquires lock for resumed runs (prevents concurrent edits to same session)
```

#### Event Factory Pattern
```python
# Each runner maintains a factory for consistent event creation
factory = EventFactory(engine_id)
factory.started(token, title, meta)          # StartedEvent
factory.action_started(action_id, kind, title, detail)
factory.action_completed(action_id, kind, title, ok, detail)
factory.completed(ok, answer, resume, error, usage)
```

#### Run Options Context Variables
```python
@contextmanager
def apply_run_options(options: EngineRunOptions | None):
    # Sets context var for this thread/task
    # EngineRunOptions: model, reasoning, system (prefix prompt)
    
@contextmanager
def apply_runtime_env(env: dict[str, str]):
    # Sets environment variables for subprocess
    # YEE88_CHAT_ID, YEE88_THREAD_ID injected by transport layer
```

---

### 1.7 Subprocess Management Protocol

**Common Flow** (implemented in `JsonlSubprocessRunner`):
1. `new_state()` - creates initial state object
2. `build_args()` - constructs CLI arguments
3. `stdin_payload()` - optional stdin data
4. `start_run()` - pre-execution hook
5. `manage_subprocess()` - async context manager
6. `iter_json_lines()` - streams JSONL from stdout
7. `decode_jsonl()` - msgspec deserialization
8. `translate()` - engine-specific → TakopiEvent conversion
9. Error handlers if subprocess exits abnormally

**JSONL Streaming Pipeline**:
```python
# Parse phase
for raw_line in iter_json_lines(stdout):
    if line is empty: continue
    
    # Attempt decode
    try:
        decoded = decode_jsonl(line)
    except DecodeError:
        events.extend(decode_error_events(raw, line, error))
        continue
    
    # Attempt translation
    try:
        events.extend(translate(decoded, state, resume, found_session))
    except Exception:
        events.extend(translate_error_events(data, error, state))
    
    # Track session ID (e.g., from StartedEvent)
    if isinstance(event, StartedEvent):
        found_session = event.resume
    
    yield events
```

---

## Part 2: Telegram Command System

### 2.1 Command Handler Architecture

**Command Types** (18 command handlers in `telegram/commands/`):
1. **Engine/Agent Management**:
   - `/agent` - View/set/clear default engine
   - `/model` - View/set/clear model override
   - `/reasoning` - View/set/clear reasoning effort

2. **Execution Control**:
   - `/dispatch` - Route incoming messages to appropriate handler

3. **Context Management**:
   - `/new` - Create new topic (conversation thread)
   - `/ctx` - View/set project context
   - `/fork` - Branch from existing conversation
   - `/topic` - Manage topic settings
   - `/chat_new` - Create chat-level context
   - `/chat_ctx` - Manage chat-level context

4. **Utilities**:
   - `/file` - Handle file operations
   - `/trigger` - Manage trigger modes (all/mentions)
   - `/plan` - Plan/reasoning support
   - `/question` - Handle user question responses
   - `/reasoning` - Configure reasoning effort
   - `/media` - Handle media group uploads
   - `/menu` - Set command menu

5. **Administrative**:
   - `/cancel` - Cancel running tasks
   - `/executor` - Execute commands

### 2.2 Executor (`telegram/commands/executor.py`)

**Key Class**: `_TelegramCommandExecutor(CommandExecutor)`

**Responsibilities**:
```python
class _TelegramCommandExecutor:
    # Constructor parameters
    exec_cfg: ExecBridgeConfig              # Transport + presenter config
    runtime: TransportRuntime               # Engine resolver
    running_tasks: RunningTasks             # Active task tracking
    scheduler: ThreadScheduler              # Event scheduling
    on_thread_known: Callback               # Thread ID notification
    engine_overrides_resolver: Callback     # Fetch engine-specific run options
    chat_id: int
    user_msg_id: int
    thread_id: int | None
    default_engine_override: EngineId | None
    resume_cache: ResumeTokenCache | None
    
    async def run_one(request: RunRequest, mode="emit") -> RunResult:
        # Modes:
        # - "emit": Send updates to transport in real-time
        # - "capture": Collect output without sending
        
    async def run_many(requests, mode="emit", parallel=False) -> list[RunResult]:
        # Parallel execution if requested
```

**Request Flow**:
```python
# User sends: "/claude fix the bug in utils/paths.py"
# 1. Parse command + args
# 2. Create RunRequest
request = RunRequest(
    prompt="fix the bug in utils/paths.py",
    engine="claude",      # or None (use default)
    context=RunContext    # or None
)

# 3. Apply defaults
request = _apply_default_engine(request)
request = _apply_default_context(request)

# 4. Resolve engine + run options
engine = runtime.resolve_engine(request.engine, request.context)
run_options = engine_overrides_resolver(engine)

# 5. Invoke _run_engine
result = _run_engine(
    exec_cfg,
    runtime,
    runner,
    context,
    on_question=handle_user_questions,
)
```

**Engine Run Options Resolution**:
1. Check engine_overrides_resolver (chat/topic-level model/reasoning)
2. Fall back to project-level default_model
3. Construct EngineRunOptions(model, reasoning, system_prompt)
4. Apply via context manager during subprocess execution

**Reasoning Support Validation**:
```python
def _reasoning_warning(engine, run_options):
    # Only codex + claude support reasoning
    # Other engines get warning if reasoning requested
    if run_options.reasoning and not supports_reasoning(engine):
        return ActionEvent(kind="note", title="reasoning not supported")
```

### 2.3 Dispatch Mechanism (`telegram/commands/dispatch.py`)

**Entry Point**: `_dispatch_command(cfg, msg, text, command_id, args_text, ...)`

**Flow**:
```python
# 1. Lookup command backend
backend = get_command(command_id, allowlist=allowlist)

# 2. Create executor for this chat
executor = _TelegramCommandExecutor(...)

# 3. Assemble command context
ctx = CommandContext(
    command=command_id,
    text=full_command_text,
    args_text=args_without_command,
    args=split_command_args(args_text),
    message=MessageRef,
    reply_to=optional_reply_ref,
    runtime=TransportRuntime,
    executor=CommandExecutor,
    plugin_config=runtime.plugin_config(command_id),
)

# 4. Invoke backend handler
result = backend.handle(ctx)

# 5. Send response
await executor.send(result.text, reply_to=result.reply_to)
```

**Command Execution Modes**:
- **Plugin-based**: Dynamically loaded from config
- **Built-in**: Agent, model, reasoning, trigger, etc.
- **Engine aliases**: Commands matching engine IDs (e.g., `/claude`)

### 2.4 Agent Management (`telegram/commands/agent.py`)

**Resolution Hierarchy**:
```
┌─ Explicit directive (in message reply)
│
├─ Topic-level default (if in thread)
│
├─ Chat-level default (if stored in chat_prefs)
│
├─ Project-level default
│
└─ Global default (config)
```

**Command Syntax**:
```
/agent                          # Show current + resolution sources
/agent set <engine>             # Set chat/topic default (admin only)
/agent clear                    # Clear overrides (admin only)
```

**Permission Model**:
- Private chats: always allowed
- Group chats: creators + admins only
- Checks via `bot.get_chat_member(chat_id, sender_id)`

### 2.5 Model/Reasoning Overrides (`telegram/commands/model.py`, `reasoning.py`)

**Override Scopes** (hierarchical):
1. **Topic-level** (thread_id → override) - highest priority
2. **Chat-level** (chat_id → override)
3. **Default** (from config) - fallback

**Command Pattern** (Model as example):
```
/model                          # Show current + overrides
/model set <model>              # Use current engine + set model
/model set <engine> <model>     # Explicit engine + model
/model clear [engine]           # Clear overrides
/model reset                     # Clear all overrides
```

**Reasoning Levels** (per engine):
```python
# Claude: "enabled", "disabled"
# Codex: "low", "medium", "high"
# Others: not supported
```

**Model Selection UI**:
```python
async def _send_model_selector(cfg, msg, engine, models):
    # For opencode engine: fetch models via "opencode models"
    # Create copyable command list:
    # /model set model1
    # /model set model2
    # (split into 4KB chunks if necessary)
```

---

## Part 3: Trigger Mode System

### 3.1 Trigger Mode Types

**File**: `telegram/trigger_mode.py`

**Modes**:
- `"all"` - Process all non-command messages (unless in mentions-only mode)
- `"mentions"` - Only process messages that mention the bot

### 3.2 Resolution Logic

```python
async def resolve_trigger_mode(
    chat_id: int,
    thread_id: int | None,
    chat_prefs: ChatPrefsStore | None,
    topic_store: TopicStateStore | None,
) -> TriggerMode:
    # 1. Check topic-level (highest priority)
    if thread_id and topic_store:
        if topic_store.get_trigger_mode(chat_id, thread_id) == "mentions":
            return "mentions"
    
    # 2. Check chat-level
    if chat_prefs:
        if chat_prefs.get_trigger_mode(chat_id) == "mentions":
            return "mentions"
    
    # 3. Default to "all"
    return "all"
```

### 3.3 Trigger Detection

```python
def should_trigger_run(
    msg: TelegramIncomingMessage,
    *,
    bot_username: str | None,
    runtime: TransportRuntime,
    command_ids: set[str],
    reserved_chat_commands: set[str],
) -> bool:
    # Case 1: Explicit mention
    if bot_username and f"@{bot_username}" in msg.text.lower():
        return True
    
    # Case 2: Reply to bot (unless implicit topic reply)
    implicit_topic_reply = (
        msg.thread_id and msg.reply_to_message_id == msg.thread_id
    )
    if msg.reply_to_is_bot and not implicit_topic_reply:
        return True
    if msg.reply_to_username.lower() == bot_username and not implicit_topic_reply:
        return True
    
    # Case 3: Command detection
    command_id, _ = parse_slash_command(msg.text)
    if command_id in {reserved_chat_commands | command_ids | engine_ids}:
        return True
    
    # Case 4: Project alias
    if command_id in runtime.project_aliases():
        return True
    
    return False
```

---

## Part 4: Cron System Architecture

### 4.1 Core Components

**Files**:
- `cron/models.py` - `CronJob` dataclass
- `cron/manager.py` - `CronManager` - persistent storage + CRUD
- `cron/scheduler.py` - `CronScheduler` - async event loop + job execution
- `cron/watch.py` - File watcher for config changes

### 4.2 CronJob Model

```python
@dataclass
class CronJob:
    id: str                         # Unique identifier
    schedule: str                   # Cron expression (or ISO datetime for one-time)
    message: str                    # Prompt to send to engine
    project: str                    # Project context (validates against registry)
    enabled: bool
    last_run: str | None            # ISO timestamp
    next_run: str | None            # ISO timestamp (pre-calculated)
    one_time: bool                  # One-time execution flag
    engine: str | None              # Explicit engine (or None for default)
    model: str | None               # Model override
```

### 4.3 CronManager (`cron/manager.py`)

**Storage**: TOML file (`~/.yee88/cron.toml`)

**API**:
```python
class CronManager:
    def __init__(self, config_dir: Path, timezone: str = "Asia/Shanghai"):
        self.file = config_dir / "cron.toml"
        self.timezone = ZoneInfo(timezone)
    
    def load(self):                 # Read jobs from TOML
    def save(self):                 # Write jobs to TOML
    def add(job: CronJob):          # Validate project + check ID uniqueness
    def remove(job_id: str) -> bool
    def get(job_id: str) -> CronJob | None
    def list() -> list[CronJob]
    def reload_jobs() -> list[str]: # Return changed job IDs
    def enable/disable(job_id: str) -> bool
    
    def get_due_jobs() -> list[CronJob]:
        # Main scheduling logic
        # - For cron jobs: use croniter to calculate next_run
        # - For one-time: check if execution time has passed
        # - Updates last_run + next_run timestamps
        # - Removes completed one-time jobs
```

### 4.4 Scheduling Logic

**Cron Expression Handling**:
```python
from croniter import croniter

# Recurring job
if not job.one_time:
    if job.last_run:
        # Resume from last execution
        itr = croniter(job.schedule, parse(job.last_run))
        next_run = itr.get_next(datetime)
    else:
        # First run: check if due within last 24 hours
        itr = croniter(job.schedule, now)
        prev_run = itr.get_prev(datetime)
        if (now - prev_run) <= timedelta(hours=24):
            queue job
```

**One-Time Execution**:
```python
if job.one_time:
    exec_time = parse(job.schedule)  # ISO timestamp
    if exec_time <= now:
        queue job
        remove from jobs after execution
```

### 4.5 CronScheduler (`cron/scheduler.py`)

**Event Loop**:
```python
class CronScheduler:
    def __init__(
        self,
        manager: CronManager,
        callback: Callable[[CronJob], Awaitable[None]],
        task_group: TaskGroup,
    ):
        self.manager = manager
        self.callback = callback      # Called for each due job
        self.task_group = task_group
        self._running_jobs: set[str]  # Prevent concurrent execution
        self._job_locks: dict[str, Lock]
    
    async def start(self):
        """Main scheduler loop"""
        self.running = True
        self.manager.load()
        
        cycle = 0
        while self.running:
            sleep_seconds = self._calculate_next_check()
            
            due_jobs = self.manager.get_due_jobs()
            for job in due_jobs:
                self.task_group.start_soon(self._run_job_safe, job)
            
            await anyio.sleep(sleep_seconds)
    
    def _calculate_next_check() -> float:
        """Calculate sleep duration to next due job"""
        min_sleep = 1.0
        max_sleep = 60.0
        
        earliest_next_run = min(
            job.next_run for job in self.manager.jobs if job.enabled
        )
        
        if not earliest_next_run:
            return max_sleep
        
        seconds_until = (earliest_next_run - now).total_seconds()
        return max(min_sleep, min(seconds_until, max_sleep))
    
    async def _run_job_safe(self, job: CronJob):
        """Execute job with locking to prevent concurrency"""
        if not self._acquire_job_lock(job.id):
            logger.warning("job.already_running")
            return
        
        try:
            await self.callback(job)
        except Exception:
            logger.error("job.failed")
        finally:
            self._release_job_lock(job.id)
```

**Concurrency Model**:
- **Per-job locking**: `anyio.Lock` prevents same job running twice
- **Task group**: Jobs run concurrently but independently
- **Non-blocking**: Scheduler continues checking while jobs execute

### 4.6 File Watcher (`cron/watch.py`)

```python
async def watch_cron_config(
    cron_file: Path,
    manager: CronManager,
    on_reload: Callable[[list[str]], Awaitable[None]] | None = None,
) -> None:
    """Watch cron.toml for changes and reload"""
    async for changes in awatch(str(cron_file)):
        for change_type, path in changes:
            if Path(path).name == "cron.toml":
                await anyio.sleep(0.2)  # Debounce
                
                changed_jobs = manager.reload_jobs()
                if changed_jobs and on_reload:
                    await on_reload(changed_jobs)
```

**Integration**:
- Hot-reload on file changes (without restarting scheduler)
- Debounce window: 200ms
- Calls `on_reload(changed_job_ids)` for downstream notification

---

## Part 5: Comparison Matrix

### 5.1 Runner Protocol Comparison

| Aspect | Claude | OpenCode | Codex | Pi |
|--------|--------|----------|-------|-----|
| **Protocol** | JSONL (args) | JSONL (args) | JSONL (stdin) | JSONL (args) |
| **Prompt Delivery** | `-- PROMPT` | `-- PROMPT` | stdin | `PROMPT` arg |
| **Session ID Format** | `ses_XXXXX` | `ses_XXXXX` | `thread_id` | Path or short ID |
| **Resume Syntax** | `--resume TOKEN` | `--session TOKEN` | `resume ID -` | `--session TOKEN` |
| **Tool Kind Inference** | Yes (name + input) | Yes (name) | Item type | Yes (name) |
| **Thinking Blocks** | Yes (as notes) | No | No | No |
| **File Changes** | Yes | Yes | Yes | Yes |
| **Web Search** | Yes | Yes | Yes | No |
| **Questions** | Yes | Yes | No | No |
| **Usage Tracking** | Yes (cost + time) | Yes | Yes (per-turn) | Yes |
| **Concurrency Control** | Optional billing env | Default | Default | Provider override |

### 5.2 Event Translation Differences

**Common Event Types**:
- `StartedEvent` - from session initialization
- `ActionStartedEvent` - tool invocation
- `ActionCompletedEvent` - tool result
- `TextDeltaEvent` - incremental text (OpenCode only)
- `CompletedEvent` - run completion

**Engine-Specific**:
- **Claude**: Thinking blocks → notes, detailed usage
- **OpenCode**: Text deltas, step-based reasoning
- **Codex**: Reconnection attempts, turn-based structure
- **Pi**: Session ID promotion, message role parsing

### 5.3 Error Handling Patterns

**Common**:
- Subprocess exit code handling
- JSONL parse error suppression (log + continue)
- Event translation error fallback

**Differences**:
- **Claude**: Validates session_id presence before completion
- **OpenCode**: Checks `saw_step_finish` flag for graceful vs error exit
- **Codex**: Tracks `final_answer` to preserve state
- **Pi**: Distinguishes error states via stop_reason (error, aborted)

---

## Part 6: Event Model Architecture

### 6.1 Event Hierarchy

```
TakopiEvent (ABC)
├── StartedEvent
│   ├── engine: EngineId
│   ├── resume: ResumeToken
│   ├── title: str
│   └── meta: dict[str, Any]
│
├── ActionEvent
│   ├── engine: EngineId
│   ├── action: Action
│   ├── phase: "started" | "updated" | "completed"
│   ├── ok: bool | None
│   ├── message: str | None
│   └── level: "debug" | "info" | "warning" | "error"
│
├── CompletedEvent
│   ├── engine: EngineId
│   ├── ok: bool
│   ├── answer: str
│   ├── resume: ResumeToken | None
│   ├── error: str | None
│   └── usage: dict[str, Any]
│
├── TextDeltaEvent
│   ├── engine: EngineId
│   └── snapshot: str (accumulated text)
│
└── TextFinishedEvent
    ├── engine: EngineId
    └── text: str (final text)
```

### 6.2 Action Model

```python
@dataclass
class Action:
    id: str                         # Unique within run
    kind: ActionKind               # "command", "file_change", "tool", etc.
    title: str                     # Display name
    detail: dict[str, Any]         # Tool-specific data
                                   # - "name": tool_name
                                   # - "input": tool_input
                                   # - "changes": [{"path": ..., "kind": ...}]
                                   # - "result": result_data
                                   # - "is_error": bool
```

---

## Part 7: Key Design Patterns

### 7.1 State Management

**Per-Run State Objects**:
```python
# Each runner maintains state during execution
state = runner.new_state(prompt, resume)

# State evolves as events stream in
for event in translate(...):
    if isinstance(event, StartedEvent):
        state.session_id = event.resume.value
    elif isinstance(event, ActionStartedEvent):
        state.pending_actions[action.id] = action
```

**Session Locking**:
```python
# Prevent concurrent modifications to same session
lock = runner.lock_for(resume_token)
async with lock:
    async for event in runner.run(prompt, resume_token):
        yield event
```

### 7.2 Context Variable Injection

**Run Options**:
```python
@contextmanager
def apply_run_options(options: EngineRunOptions):
    token = _RUN_OPTIONS.set(options)
    try:
        yield
    finally:
        _RUN_OPTIONS.reset(token)

# Inside runner.build_args()
run_options = get_run_options()
if run_options.model:
    args.extend(["--model", run_options.model])
```

**Runtime Environment**:
```python
@contextmanager
def apply_runtime_env(env: dict[str, str]):
    token = _RUNTIME_ENV.set(env)
    try:
        yield
    finally:
        _RUNTIME_ENV.reset(token)

# Inside runner.env()
runtime = get_runtime_env()
merged = dict(os.environ)
merged.update(runtime)  # YEE88_CHAT_ID, etc.
return merged
```

### 7.3 Visitor Pattern for Events

**EventFactory**:
```python
class EventFactory:
    def __init__(self, engine: EngineId):
        self.engine = engine
    
    def started(self, token, title, meta):
        return StartedEvent(engine=self.engine, resume=token, ...)
    
    def action_started(self, action_id, kind, title):
        return ActionEvent(engine=self.engine, phase="started", ...)
```

**Translation Functions**:
```python
def translate_event(raw_event, *, state, factory):
    match raw_event:
        case ToolUseBlock():
            action = extract_action(raw_event)
            state.pending_actions[action.id] = action
            return [factory.action_started(...)]
        case ToolResultBlock():
            action = state.pending_actions.pop(...)
            return [factory.action_completed(...)]
```

---

## Part 8: Extensibility Points

### 8.1 Adding a New Runner

1. **Create `runners/newengine.py`**:
   ```python
   @dataclass(slots=True)
   class NewEngineRunner(ResumeTokenMixin, JsonlSubprocessRunner):
       engine: EngineId = "newengine"
       resume_re = re.compile(r"...")
       
       def command(self) -> str:
           return "newengine"
       
       def build_args(self, prompt, resume, *, state):
           return ["--format", "json", "--", prompt]
       
       def translate(self, data, *, state, resume, found_session):
           # Convert engine-specific events to TakopiEvent
           return [...]
   
   BACKEND = EngineBackend(
       id="newengine",
       build_runner=build_runner,
       install_cmd="npm install -g newengine-cli",
   )
   ```

2. **Register in config**:
   ```toml
   [engines.newengine]
   enabled = true
   ```

### 8.2 Adding a New Command

1. **Create handler function**:
   ```python
   async def _handle_newcmd_command(
       cfg: TelegramBridgeConfig,
       msg: TelegramIncomingMessage,
       args_text: str,
   ) -> None:
       reply = make_reply(cfg, msg)
       await reply(text="response")
   ```

2. **Register as plugin or built-in**
3. **Add to command menu if needed**

### 8.3 Adding Custom Run Options

1. **Extend `EngineRunOptions`**:
   ```python
   @dataclass(frozen=True)
   class EngineRunOptions:
       model: str | None
       reasoning: str | None
       system: str | None
       custom_field: str | None  # New field
   ```

2. **Apply in runner.build_args()**:
   ```python
   run_options = get_run_options()
   if run_options.custom_field:
       args.extend(["--custom", run_options.custom_field])
   ```

---

## Part 9: Critical Dependencies & Assumptions

### 9.1 External Tools Required
- `claude` - Claude CLI (npm install @anthropic-ai/claude-code)
- `opencode` - OpenCode CLI (npm install opencode-ai)
- `codex` - Codex CLI (npm install @openai/codex)
- `pi` - Pi Coding Agent (npm install @mariozechner/pi-coding-agent)

### 9.2 Protocol Assumptions
- All runners output JSONL to stdout
- Session IDs are stable across resumed runs
- Events must be self-contained (no cross-line state)
- Tools use consistent naming conventions

### 9.3 Timezone Handling
- Cron scheduler uses `ZoneInfo("Asia/Shanghai")` by default
- All timestamps are ISO format
- One-time jobs convert to datetime for comparison

### 9.4 File Storage
- Cron jobs persisted to `~/.yee88/cron.toml`
- Pi sessions stored in `~/.pi/agent/sessions/`
- Chat preferences in config directory
- Topic state in database (if configured)

---

## Part 10: Performance Characteristics

### 10.1 Concurrency Model
- **Per-session locks**: Prevents race conditions on resume
- **Non-blocking I/O**: Uses anyio for async subprocess management
- **Task groups**: Allows parallel execution of independent jobs
- **Weak references**: Session locks auto-cleanup when token GCed

### 10.2 Memory Usage
- **JSONL streaming**: Events processed line-by-line (not buffered)
- **State objects**: Minimal per-run (action tracking only)
- **WeakValueDictionary**: Session locks freed automatically
- **One-time jobs removed**: Processed jobs purged from memory

### 10.3 Latency
- **Scheduler cycle**: 1-60 second sleep (adaptive to next job)
- **Debounce**: 200ms on cron file changes
- **Event translation**: Minimal overhead (match statements)
- **Subprocess startup**: Engine-dependent (typically 1-5s)

---

## Conclusion

Takopi's architecture demonstrates sophisticated pattern usage:

1. **Abstraction**: Multiple engines reduced to common JSONL + event interface
2. **Extensibility**: New runners/commands easy to add without core changes
3. **Safety**: Session locking prevents concurrent access; context vars enable injection
4. **Observability**: Rich event model tracks execution with per-action granularity
5. **Scheduling**: Flexible cron system with hot-reload and async execution
6. **Integration**: Tight Telegram coupling via commands, overrides, and context

The system prioritizes **flexibility** (multiple engines, configurable behavior) and **safety** (session locking, permission checks) over simplicity, making it suitable for production multi-user environments.
