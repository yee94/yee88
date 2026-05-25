"""CodeBuddy Code runner.

CodeBuddy's stream-json CLI protocol is a near-exact superset of Claude Code's.
This module mirrors :mod:`yee88.runners.claude` with the minimal set of
differences:

1. command name is ``codebuddy``
2. resume line text is ``codebuddy --resume <id>`` (not ``claude --resume``)
3. CodeBuddy emits two extra event kinds (``system.subtype="status"`` and
   ``file-history-snapshot``); both are ignored by ``translate_codebuddy_event``
4. ``ANTHROPIC_API_KEY`` is stripped from the child env so CodeBuddy uses its
   own (Tencent) backend instead of forwarding the key to Anthropic
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..events import EventFactory
from ..logging import get_logger
from ..model import Action, ActionKind, EngineId, ResumeToken, TakopiEvent
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import codebuddy as cb_schema
from .run_options import get_run_options
from .tool_actions import tool_input_path, tool_kind_and_title

logger = get_logger(__name__)

ENGINE: EngineId = "codebuddy"
DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Edit", "Write"]

# Fallback model when the user has not configured ``[codebuddy].model`` in
# ``yee88.toml``. Mirrors the convention of the ``[opencode]`` block: users can
# override freely; we just want a sensible out-of-the-box choice so a fresh
# install runs without extra setup.
DEFAULT_MODEL = "claude-sonnet-4.6"

_RESUME_RE = re.compile(
    r"(?im)^\s*`?codebuddy\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$"
)


@dataclass(slots=True)
class CodeBuddyStreamState:
    factory: EventFactory = field(default_factory=lambda: EventFactory(ENGINE))
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0


def _normalize_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return str(content)


def _coerce_comma_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value if item is not None]
        joined = ",".join(part for part in parts if part)
        return joined or None
    text = str(value)
    return text or None


def _tool_kind_and_title(
    name: str, tool_input: dict[str, Any]
) -> tuple[ActionKind, str]:
    return tool_kind_and_title(name, tool_input, path_keys=("file_path", "path"))


def _tool_action(
    content: cb_schema.StreamToolUseBlock,
    *,
    parent_tool_use_id: str | None,
) -> Action:
    tool_id = content.id
    tool_name = str(content.name or "tool")
    tool_input = content.input

    kind, title = _tool_kind_and_title(tool_name, tool_input)

    detail: dict[str, Any] = {
        "name": tool_name,
        "input": tool_input,
    }
    if parent_tool_use_id:
        detail["parent_tool_use_id"] = parent_tool_use_id

    if kind == "file_change":
        path = tool_input_path(tool_input, path_keys=("file_path", "path"))
        if path:
            detail["changes"] = [{"path": path, "kind": "update"}]

    return Action(id=tool_id, kind=kind, title=title, detail=detail)


def _tool_result_event(
    content: cb_schema.StreamToolResultBlock,
    *,
    action: Action,
    factory: EventFactory,
) -> TakopiEvent:
    is_error = content.is_error is True
    normalized = _normalize_tool_result(content.content)
    detail = action.detail | {
        "tool_use_id": content.tool_use_id,
        "result_preview": normalized,
        "result_len": len(normalized),
        "is_error": is_error,
    }
    return factory.action_completed(
        action_id=action.id,
        kind=action.kind,
        title=action.title,
        ok=not is_error,
        detail=detail,
    )


def _extract_error(event: cb_schema.StreamResultMessage) -> str | None:
    if event.is_error:
        if isinstance(event.result, str) and event.result:
            return event.result
        subtype = event.subtype
        if subtype:
            return f"codebuddy run failed ({subtype})"
        return "codebuddy run failed"
    return None


def _usage_payload(event: cb_schema.StreamResultMessage) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in (
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
    ):
        value = getattr(event, key, None)
        if value is not None:
            usage[key] = value
    if event.usage is not None:
        usage["usage"] = event.usage
    return usage


def translate_codebuddy_event(
    event: cb_schema.StreamJsonMessage,
    *,
    title: str,
    state: CodeBuddyStreamState,
    factory: EventFactory,
) -> list[TakopiEvent]:
    match event:
        case cb_schema.FileHistorySnapshot():
            # codebuddy-only book-keeping row; no observable event for the bridge
            return []
        case cb_schema.StreamSystemMessage(subtype=subtype):
            if subtype != "init":
                # ignore subtype="status" and any other future system pings
                return []
            session_id = event.session_id
            if not session_id:
                return []
            meta: dict[str, Any] = {}
            for key in (
                "cwd",
                "tools",
                "permissionMode",
                "output_style",
                "apiKeySource",
                "mcp_servers",
            ):
                value = getattr(event, key, None)
                if value is not None:
                    meta[key] = value
            model = event.model
            token = ResumeToken(engine=ENGINE, value=session_id)
            event_title = str(model) if isinstance(model, str) and model else title
            return [factory.started(token, title=event_title, meta=meta or None)]
        case cb_schema.StreamAssistantMessage(
            message=message, parent_tool_use_id=parent_tool_use_id
        ):
            out: list[TakopiEvent] = []
            for content in message.content:
                match content:
                    case cb_schema.StreamToolUseBlock():
                        action = _tool_action(
                            content, parent_tool_use_id=parent_tool_use_id
                        )
                        state.pending_actions[action.id] = action
                        out.append(
                            factory.action_started(
                                action_id=action.id,
                                kind=action.kind,
                                title=action.title,
                                detail=action.detail,
                            )
                        )
                    case cb_schema.StreamThinkingBlock(
                        thinking=thinking, signature=signature
                    ):
                        if not thinking:
                            continue
                        state.note_seq += 1
                        action_id = f"codebuddy.thinking.{state.note_seq}"
                        detail: dict[str, Any] = {}
                        if parent_tool_use_id:
                            detail["parent_tool_use_id"] = parent_tool_use_id
                        if signature:
                            detail["signature"] = signature
                        out.append(
                            factory.action_completed(
                                action_id=action_id,
                                kind="note",
                                title=thinking,
                                ok=True,
                                detail=detail,
                            )
                        )
                    case cb_schema.StreamTextBlock(text=text):
                        if text:
                            state.last_assistant_text = text
                    case _:
                        continue
            return out
        case cb_schema.StreamUserMessage(message=message):
            if not isinstance(message.content, list):
                return []
            out: list[TakopiEvent] = []
            for content in message.content:
                if not isinstance(content, cb_schema.StreamToolResultBlock):
                    continue
                tool_use_id = content.tool_use_id
                action = state.pending_actions.pop(tool_use_id, None)
                if action is None:
                    action = Action(
                        id=tool_use_id,
                        kind="tool",
                        title="tool result",
                        detail={},
                    )
                out.append(
                    _tool_result_event(content, action=action, factory=factory)
                )
            return out
        case cb_schema.StreamResultMessage():
            ok = not event.is_error
            result_text = event.result or ""
            if ok and not result_text and state.last_assistant_text:
                result_text = state.last_assistant_text

            resume = ResumeToken(engine=ENGINE, value=event.session_id)
            error = None if ok else _extract_error(event)
            usage = _usage_payload(event)

            return [
                factory.completed(
                    ok=ok,
                    answer=result_text,
                    resume=resume,
                    error=error,
                    usage=usage or None,
                )
            ]
        case _:
            return []


@dataclass(slots=True)
class CodeBuddyRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    codebuddy_cmd: str = "codebuddy"
    model: str | None = None
    allowed_tools: list[str] | None = None
    dangerously_skip_permissions: bool = False
    session_title: str = "codebuddy"
    logger = logger

    def _build_args(
        self, prompt: str, resume: ResumeToken | None
    ) -> list[str]:
        run_options = get_run_options()
        args: list[str] = ["-p", "--output-format", "stream-json", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        model = self.model
        if run_options is not None and run_options.model:
            model = run_options.model
        if model is not None:
            args.extend(["--model", str(model)])
        allowed_tools = _coerce_comma_list(self.allowed_tools)
        if allowed_tools is not None:
            args.extend(["--allowedTools", allowed_tools])
        if self.dangerously_skip_permissions is True:
            args.append("--dangerously-skip-permissions")
        # System prompt only applies to the first run of a session
        if resume is None and run_options is not None and run_options.system:
            prompt = f"{run_options.system}\n\n---\n\n{prompt}"
        args.append("--")
        args.append(prompt)
        return args

    def command(self) -> str:
        return self.codebuddy_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        return self._build_args(prompt, resume)

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        return None

    def env(self, *, state: Any) -> dict[str, str] | None:
        # CodeBuddy uses its own (Tencent) endpoint; never leak ANTHROPIC_API_KEY.
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    def new_state(
        self, prompt: str, resume: ResumeToken | None
    ) -> CodeBuddyStreamState:
        return CodeBuddyStreamState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: CodeBuddyStreamState,
    ) -> None:
        pass

    def decode_jsonl(
        self,
        *,
        line: bytes,
    ) -> cb_schema.StreamJsonMessage:
        return cb_schema.decode_stream_json_line(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: CodeBuddyStreamState,
    ) -> list[TakopiEvent]:
        if isinstance(error, msgspec.DecodeError):
            self.get_logger().warning(
                "jsonl.msgspec.invalid",
                tag=self.tag(),
                error=str(error),
                error_type=error.__class__.__name__,
            )
            return []
        return super().decode_error_events(
            raw=raw,
            line=line,
            error=error,
            state=state,
        )

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: CodeBuddyStreamState,
    ) -> list[TakopiEvent]:
        return []

    def translate(
        self,
        data: cb_schema.StreamJsonMessage,
        *,
        state: CodeBuddyStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        return translate_codebuddy_event(
            data,
            title=self.session_title,
            state=state,
            factory=state.factory,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CodeBuddyStreamState,
        stderr: str = "",
    ) -> list[TakopiEvent]:
        message = f"codebuddy failed (rc={rc})."
        if stderr:
            message = f"{message}\n{stderr.strip()[-500:]}"
        resume_for_completed = found_session or resume
        return [
            self.note_event(message, state=state, ok=False),
            state.factory.completed_error(
                error=message,
                resume=resume_for_completed,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CodeBuddyStreamState,
        stderr: str = "",
    ) -> list[TakopiEvent]:
        if not found_session:
            message = "codebuddy finished but no session_id was captured"
            if stderr:
                message = f"{message}\n{stderr.strip()[-500:]}"
            return [
                state.factory.completed_error(
                    error=message,
                    resume=resume,
                )
            ]

        message = "codebuddy finished without a result event"
        if stderr:
            message = f"{message}\n{stderr.strip()[-500:]}"
        return [
            state.factory.completed_error(
                error=message,
                answer=state.last_assistant_text or "",
                resume=found_session,
            )
        ]


def build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    codebuddy_cmd = shutil.which("codebuddy") or "codebuddy"

    # If the user did not put `model = "..."` under `[codebuddy]` in yee88.toml
    # (or set it explicitly to None), use DEFAULT_MODEL so a fresh install still
    # runs without any tweaking. The user can override anytime with
    # `yee88 config set codebuddy.model <id>` or via /model in chat.
    model = config.get("model") or DEFAULT_MODEL
    if "allowed_tools" in config:
        allowed_tools = config.get("allowed_tools")
    else:
        allowed_tools = DEFAULT_ALLOWED_TOOLS
    # CodeBuddy runs as a non-interactive Telegram bot — there is no human to
    # answer permission prompts mid-run. Default to skipping permissions so a
    # fresh install just works; users can still opt out with
    # `dangerously_skip_permissions = false` in `[codebuddy]`.
    dsp_value = config.get("dangerously_skip_permissions")
    dangerously_skip_permissions = True if dsp_value is None else dsp_value is True
    title = str(model)

    return CodeBuddyRunner(
        codebuddy_cmd=codebuddy_cmd,
        model=model,
        allowed_tools=allowed_tools,
        dangerously_skip_permissions=dangerously_skip_permissions,
        session_title=title,
    )


BACKEND = EngineBackend(
    id="codebuddy",
    build_runner=build_runner,
    install_cmd="npm install -g @tencent-ai/codebuddy-code",
)
