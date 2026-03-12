# changelog

## v0.10.5 (2026-03-12)

### fixes

- handoff: 在未注册项目的目录下自动补注册项目配置，避免新建 handoff topic 后继续对话时报 `unknown project`
- handoff: 无法自动注册项目时降级为无项目上下文，避免写入失效 topic context

## v0.10.4 (2026-03-11)

### fixes

- 移除 cron 调度器对单个定时任务的 180 秒执行超时，长任务改为持续执行直到自然结束或失败
- Telegram 后端运行的定时任务改为使用无超时 HTTP 客户端，避免约 2 分钟时因请求超时卡住

## v0.10.3 (2026-03-08)

### features

- **多模态图文支持**：AI 回复中的 Markdown 图片自动通过 sendPhoto 发送为真实图片；用户发送带 caption 的图片/文件自动上传并传递给 AI
- **question tool 支持**：AI 引擎的 question action 自动转化为 Telegram inline keyboard，支持按钮回答和自由输入
- **智能问候语**：启动时根据时间段、工作日/周末、长时间未使用等条件发送差异化问候
- **状态栏 footer 重构**：使用精简 emoji 展示运行状态（⏳/✅/❌/⏹），模型名称智能美化，上下文路径用 📂 展示
- **session resume cache**：新增 resume_cache 模块缓存 session token，加速会话恢复

### fixes

- 修复带 caption 的文件/照片上传返回 usage 错误
- 修复纯数字 prompt 导致 opencode CLI 崩溃
- 修复 question callback 返回 resume token 恢复 session
- 新增 _BUILTIN_DIRECTIVES 机制，在 system prompt 末尾注入不可覆盖的内置指令
- 简化 telegram resume handling 逻辑

## v0.9.10 (2026-03-07)

### features

- **项目级独立配置**：新增 default_model（项目独立模型）、session_mode（stateless/chat 模式）
- 修复 topic 模型隔离：chat 级别引擎覆盖不再泄漏到 topic

## v0.9.5 (2026-03-04)

### features

- **send-file 命令**：新增 `yee88 send-file` CLI 命令，AI 引擎可主动向 Telegram 发送文件/图片
- **运行时环境注入**：自动注入 YEE88_CHAT_ID 和 YEE88_THREAD_ID 到引擎子进程

### fixes

- handoff: topic 被删除时自动重试新 topic
- handoff: 创建 topic 前校验项目有效性
- handoff: 适配 OpenCode SQLite 存储格式

## v0.9.0 (2026-02-15)

### features

- **cron 独立 session**：定时任务支持独立会话隔离和可配置的 engine/model
- **cron engine/model 字段**：CronJob 模型新增 engine 和 model 配置

### fixes

- cron: 全量修复 Cron 系统关键问题
- cron: 漏执行检测改为检查过去 24 小时而非仅当天

## v0.8.0 (2026-02-08)

### features

- **cron 增强**：支持独立 session 和可配置引擎/模型的定时任务
- handoff 命令输出信息优化

## v0.7.1 (2026-02-01)

### features

- **`/fork` 命令**：将当前 topic 的上下文和 session 状态分叉到新 topic，支持自动编号（fork #1, fork #2, ...）
- README 全面改写为中文，新增 `npx skills add yee94/yee88` 一键安装引导

## v0.7.0 (2026-02-01)

### features

- add `/handoff` command to transfer desktop session to mobile seamlessly

### fixes

- fix scheduled task trigger issues in cron scheduler

## v0.6.3 (2026-02-01)

### features

- add `/model reset` subcommand to clear model overrides

## v0.6.2 (2026-01-31)

### changes

- optimize system_prompt handling: only prepend on first run to save tokens

## v0.6.1 (2026-01-31)

### fixes

- correct system_prompt syntax error in settings

## v0.6.0 (2026-01-31)

### features

- add one-time task execution support in cron scheduler

### docs

- update yee88 skill documentation

### changes

- update system prompt wording

## v0.5.0 (2026-01-31)

### changes

- fork baseline from upstream v0.21.4
- minor fixes and improvements

---

## upstream changelog (v0.21.4 and earlier)

## v0.21.4 (2026-01-22)

### changes

- add allowed user gate to telegram [#179](https://github.com/banteg/yee88/pull/179)

## v0.21.3 (2026-01-21)

### fixes

- ignore implicit topic root replies in telegram [#175](https://github.com/banteg/yee88/pull/175)

## v0.21.2 (2026-01-20)

### fixes

- clear chat sessions on cwd change [#172](https://github.com/banteg/yee88/pull/172)

### docs

- add yee88-slack plugin to reference [#168](https://github.com/banteg/yee88/pull/168)

## v0.21.1 (2026-01-18)

### fixes

- separate telegram voice transcription client [#166](https://github.com/banteg/yee88/pull/166)
- disable telegram link previews by default [#160](https://github.com/banteg/yee88/pull/160)

### docs

- align engine terminology in telegram and docs [#162](https://github.com/banteg/yee88/pull/162)
- add yee88-discord plugin to plugins reference [#164](https://github.com/banteg/yee88/pull/164)

## v0.21.0 (2026-01-16)

### changes

- add `yee88 config` subcommand [#153](https://github.com/banteg/yee88/pull/153)
- make telegram /ctx work everywhere [#159](https://github.com/banteg/yee88/pull/159)
- improve telegram command planning and testability [#158](https://github.com/banteg/yee88/pull/158)
- simplify telegram loop and jsonl runner [#155](https://github.com/banteg/yee88/pull/155)
- refactor telegram schemas and parsing with msgspec [#156](https://github.com/banteg/yee88/pull/156)

### tests

- improve coverage and raise threshold to 80% [#154](https://github.com/banteg/yee88/pull/154)
- stabilize mutmut runs and extend telegram coverage [#157](https://github.com/banteg/yee88/pull/157)

### docs

- add opengraph meta fallbacks [#150](https://github.com/banteg/yee88/pull/150)

## v0.20.0 (2026-01-15)

### changes

- add telegram mentions-only trigger mode [#142](https://github.com/banteg/yee88/pull/142)
- add telegram /model and /reasoning overrides [#147](https://github.com/banteg/yee88/pull/147)
- coalesce forwarded telegram messages [#146](https://github.com/banteg/yee88/pull/146)
- export plugin utilities for transport development [#137](https://github.com/banteg/yee88/pull/137)

### fixes

- handle forwarded uploads for telegram [#149](https://github.com/banteg/yee88/pull/149)
- preserve directives for voice transcripts [#141](https://github.com/banteg/yee88/pull/141)
- resolve claude.cmd via shutil.which on windows [#124](https://github.com/banteg/yee88/pull/124)

### docs

- add yee88-scripts plugin to plugins list [#140](https://github.com/banteg/yee88/pull/140)

## v0.19.0 (2026-01-15)

### changes

- overhaul onboarding with persona-based setup flows [#132](https://github.com/banteg/yee88/pull/132)
- add queued cancel placeholder for Telegram runs [#136](https://github.com/banteg/yee88/pull/136)
- prefix Telegram voice transcriptions for agent awareness [#135](https://github.com/banteg/yee88/pull/135)

### docs

- refresh onboarding docs with new widgets and hero flow [#138](https://github.com/banteg/yee88/pull/138)
- fix docs site mobile layout and font consistency [#139](https://github.com/banteg/yee88/pull/139)
- link to yee88.dev docs site

## v0.18.0 (2026-01-13)

### changes

- add per-chat and per-topic default agent via `/agent set` command [#109](https://github.com/banteg/yee88/pull/109)
- add session resume shorthand for pi runner [#113](https://github.com/banteg/yee88/pull/113)
- expose `sender_id` and `raw` fields on `MessageRef` for plugins [#112](https://github.com/banteg/yee88/pull/112)

### fixes

- recreate stale topic bindings when topic is deleted and recreated [#127](https://github.com/banteg/yee88/pull/127)
- use stdout session header for pi runner [#126](https://github.com/banteg/yee88/pull/126)

### docs

- restructure docs into diataxis format and switch to zensical [#121](https://github.com/banteg/yee88/pull/121) [#125](https://github.com/banteg/yee88/pull/125)

## v0.17.1 (2026-01-12)

### fixes

- fix telegram /new command crash [#106](https://github.com/banteg/yee88/pull/106)
- track telegram sessions for plugin runs [#107](https://github.com/banteg/yee88/pull/107)
- align telegram prompt upload resume flow [#105](https://github.com/banteg/yee88/pull/105)

## v0.17.0 (2026-01-12)

### changes

- add chat session mode (`session_mode = "chat"`) for auto-resume per chat without replying, reset with `/new` [#102](https://github.com/banteg/yee88/pull/102)
- add `message_overflow = "split"` to send long responses as multiple messages instead of trimming [#101](https://github.com/banteg/yee88/pull/101)
- add `show_resume_line` option to hide resume lines when auto-resume is available [#100](https://github.com/banteg/yee88/pull/100)
- add `auto_put_mode = "prompt"` to start a run with the caption after uploading a file [#97](https://github.com/banteg/yee88/pull/97)
- expose `thread_id` to plugins via run context [#99](https://github.com/banteg/yee88/pull/99)
- use tomli-w for config serialization [#103](https://github.com/banteg/yee88/pull/103)
- add `voice_transcription_model` setting for local whisper servers [#98](https://github.com/banteg/yee88/pull/98)

### docs

- document chat sessions, message overflow, and voice transcription model settings

## v0.16.0 (2026-01-12)

### fixes

- harden telegram file transfer handling [#84](https://github.com/banteg/yee88/pull/84)

### changes

- simplify runtime, config, and telegram internals [#85](https://github.com/banteg/yee88/pull/85)
- refactor telegram boundary types [#90](https://github.com/banteg/yee88/pull/90)

### docs

- add tips section to user guide
- rework readme

## v0.15.0 (2026-01-11)

### changes

- add telegram file transfer support [#83](https://github.com/banteg/yee88/pull/83)

### docs

- document telegram file transfers [#83](https://github.com/banteg/yee88/pull/83)

## v0.14.1 (2026-01-10)

### changes

- add topic scope and thread-aware replies for telegram topics [#81](https://github.com/banteg/yee88/pull/81)

### docs

- update telegram topics docs and user guide for topic scoping [#81](https://github.com/banteg/yee88/pull/81)

## v0.14.0 (2026-01-10)

### changes

- add telegram forum topics support with `/topic` command for binding threads to projects/branches, persistent resume tokens per topic, and `/ctx` for inspecting or updating bindings [#80](https://github.com/banteg/yee88/pull/80)
- add inline cancel button to progress messages [#79](https://github.com/banteg/yee88/pull/79)
- add config hot-reload via watchfiles [#78](https://github.com/banteg/yee88/pull/78)

### docs

- add user guide and telegram topics documentation [#80](https://github.com/banteg/yee88/pull/80)

## v0.13.0 (2026-01-09)

### changes

- add per-project chat routing [#76](https://github.com/banteg/yee88/pull/76)

### fixes

- hardcode codex exec flags [#75](https://github.com/banteg/yee88/pull/75)
- reuse project root for current branch when resolving worktrees [#77](https://github.com/banteg/yee88/pull/77)

### docs

- normalize casing in the readme and changelog

## v0.12.0 (2026-01-09)

### changes

- add optional telegram voice note transcription (routes transcript like typed text) [#74](https://github.com/banteg/yee88/pull/74)

### fixes

- fix plugin allowlist matching and windows session paths [#72](https://github.com/banteg/yee88/pull/72)

### docs

- document telegram voice transcription settings [#74](https://github.com/banteg/yee88/pull/74)

## v0.11.0 (2026-01-08)

### changes

- add entrypoint-based plugins for engines/transports plus a `yee88 plugins` command and public API docs [#71](https://github.com/banteg/yee88/pull/71)

### fixes

- create pi sessions under the run base dir [#68](https://github.com/banteg/yee88/pull/68)
- skip git repo checks for codex runs [#66](https://github.com/banteg/yee88/pull/66)

## v0.10.0 (2026-01-08)

### changes

- add transport registry with `--transport` overrides and a `yee88 transports` command [#69](https://github.com/banteg/yee88/pull/69)
- migrate config loading to pydantic-settings and move telegram credentials under `[transports.telegram]` [#65](https://github.com/banteg/yee88/pull/65)
- include project aliases in the telegram slash-command menu with validation and limits [#67](https://github.com/banteg/yee88/pull/67)

### fixes

- validate worktree roots instead of treating nested paths as worktrees [#63](https://github.com/banteg/yee88/pull/63)
- harden onboarding with clearer config errors, safe backups, and refreshed command menu wording [#70](https://github.com/banteg/yee88/pull/70)

### docs

- add architecture and lifecycle diagrams
- call out the default worktrees directory [#64](https://github.com/banteg/yee88/pull/64)
- document the transport registry and onboarding changes [#69](https://github.com/banteg/yee88/pull/69)

## v0.9.0 (2026-01-07)

### projects and worktrees

- register repos with `yee88 init <alias>` and target them via `/project` directives
- route runs to git worktrees with `@branch` — yee88 resolves or creates worktrees automatically
- replies preserve context via `ctx: project @branch` footers, no need to repeat directives
- set `default_project` to skip the `/project` prefix entirely
- per-project `default_engine` and `worktree_base` configuration

### changes

- transport/presenter protocols plus transport-agnostic `exec_bridge`
- move telegram polling + wiring into `yee88.telegram` with transport/presenter adapters
- list configured projects in the startup banner

### fixes

- render `ctx:` footer lines consistently (backticked + hard breaks) and include them in final messages

### breaking

- remove `yee88.bridge`; use `yee88.runner_bridge` and `yee88.telegram` instead

### docs

- add a projects/worktrees guide and document `yee88 init` behavior in the readme

## v0.8.0 (2026-01-05)

### changes

- queue telegram requests with rate limits and retry-after backoff [#54](https://github.com/banteg/yee88/pull/54)

### docs

- improve documentation coverage [#52](https://github.com/banteg/yee88/pull/52)
- align runner guide with factory pattern
- add missing pr links in the changelog

## v0.7.0 (2026-01-04)

### changes

- migrate logging to structlog with structured pipelines and redaction [#46](https://github.com/banteg/yee88/pull/46)
- add msgspec schemas for jsonl decoding across runners [#37](https://github.com/banteg/yee88/pull/37)

## v0.6.0 (2026-01-03)

### changes

- interactive onboarding: run `yee88` to set up bot token, chat id, and default engine via guided prompts [#39](https://github.com/banteg/yee88/pull/39)
- lockfile to prevent multiple yee88 instances from racing the same bot token [#30](https://github.com/banteg/yee88/pull/30)
- re-run onboarding anytime with `yee88 --onboard`

## v0.5.3 (2026-01-02)

### changes

- default claude allowed tools to `["Bash", "Read", "Edit", "Write"]` when not configured [#29](https://github.com/banteg/yee88/pull/29)

## v0.5.2 (2026-01-02)

### changes

- show not installed agents in the startup banner (while hiding them from slash commands)

### fixes

- treat codex reconnect notices as non-fatal progress updates instead of errors [#27](https://github.com/banteg/yee88/pull/27)
- avoid crashes when codex tool/file-change events omit error fields [#27](https://github.com/banteg/yee88/pull/27)

## v0.5.1 (2026-01-02)

### changes

- relax telegram ACL to check chat id only, enabling use in group chats and channels [#26](https://github.com/banteg/yee88/pull/26)
- improve onboarding documentation and add tests [#25](https://github.com/banteg/yee88/pull/25)

## v0.5.0 (2026-01-02)

### changes

- add an opencode runner via the `opencode` cli with json event parsing and resume support [#22](https://github.com/banteg/yee88/pull/22)
- add a pi agent runner via the `pi` cli with jsonl streaming and resume support [#24](https://github.com/banteg/yee88/pull/24)
- document the opencode and pi runners, event mappings, and stream capture tips

### fixes

- fix path relativization so progress output does not strip sibling directories [#23](https://github.com/banteg/yee88/pull/23)
- reduce noisy debug logging from markdown_it/httpcore

## v0.4.0 (2026-01-02)

### changes

- add auto-router runner selection with configurable default engine [#15](https://github.com/banteg/yee88/pull/15)
- make auto-router the default entrypoint; subcommands or `/{engine}` prefixes override for new threads
- add `/cancel` + `/{engine}` command menu sync on startup
- show engine name in progress and final message headers
- omit progress/action log lines from final output for cleaner answers [#21](https://github.com/banteg/yee88/pull/21)

### fixes

- improve codex exec error rendering with stderr extraction [#18](https://github.com/banteg/yee88/pull/18)
- preserve markdown formatting and resume footer when trimming long responses [#20](https://github.com/banteg/yee88/pull/20)

## v0.3.0 (2026-01-01)

### changes

- add a claude code runner via the `claude` cli with stream-json parsing and resume support [#9](https://github.com/banteg/yee88/pull/9)
- auto-discover engine backends and generate cli subcommands from the registry [#12](https://github.com/banteg/yee88/pull/12)
- add `BaseRunner` session locking plus a `JsonlSubprocessRunner` helper for jsonl subprocess engines
- add jsonl stream parsing and subprocess helpers for runners
- lazily allocate per-session locks and streamline backend setup/install metadata
- improve startup message formatting and markdown rendering
- add a debug onboarding helper for setup troubleshooting

### breaking

- runner implementations must define explicit resume parsing/formatting (no implicit standard resume pattern)

### fixes

- stop leaking a hidden `engine-id` cli option on engine subcommands

### docs

- add a runner guide plus claude code docs (runner, events, stream-json cheatsheet)
- clarify the claude runner file layout and add guidance for jsonl-based runners
- document "minimal" runner mode: started+completed only, completed-only actions allowed

## v0.2.0 (2025-12-31)

### changes

- introduce runner protocol for multi-engine support [#7](https://github.com/banteg/yee88/pull/7)
  - normalized event model (`started`, `action`, `completed`)
  - actions with stable ids, lifecycle phases, and structured details
  - engine-agnostic bridge and renderer
- add `/cancel` command with progress message targeting [#4](https://github.com/banteg/yee88/pull/4)
- migrate async runtime from asyncio to anyio [#6](https://github.com/banteg/yee88/pull/6)
- stream runner events via async iterators (natural backpressure)
- per-thread job queues with serialization for same-thread runs
- render resume as `codex resume <token>` command lines
- various rendering improvements including file edits

### breaking

- require python 3.14+
- remove `--profile` flag; configure via `[codex].profile` only

### fixes

- serialize new sessions once resume token is known
- preserve resume tokens in error renders [#3](https://github.com/banteg/yee88/pull/3)
- preserve file-change paths in action events [#2](https://github.com/banteg/yee88/pull/2)
- terminate codex process groups on cancel (posix)
- correct resume command matching in bridge

## v0.1.0 (2025-12-29)

### features

- telegram bot bridge for openai codex cli via `codex exec`
- stateless session resume via `` `codex resume <token>` `` lines
- real-time progress updates with ~2s throttling
- full markdown rendering with telegram entities (markdown-it-py + sulguk)
- per-session serialization to prevent race conditions
- interactive onboarding guide for first-time setup
- codex profile configuration
- automatic telegram token redaction in logs
- cli options: `--debug`, `--final-notify`, `--version`
