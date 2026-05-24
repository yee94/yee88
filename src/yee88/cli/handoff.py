from __future__ import annotations

import os
from pathlib import Path

import anyio
import typer

from ..config import ConfigError, ensure_table, load_or_init_config, write_config
from ..config_migrations import migrate_config
from ..context import RunContext
from ..engines import list_backend_ids
from ..ids import RESERVED_CHAT_COMMANDS
from ..model import ResumeToken
from ..settings import load_settings_if_exists
from ..telegram.client import TelegramClient
from ..telegram.topic_state import TopicStateStore, resolve_state_path
from ..telegram.engine_overrides import EngineOverrides
from ..utils.git import resolve_default_base, resolve_main_worktree_root
from .handoff_sources import HandoffSessionInfo, HandoffSource
from .handoff_sources.codebuddy import CodeBuddyHandoffSource
from .handoff_sources.opencode import (
    DEFAULT_OPENCODE_DB,
    DEFAULT_OPENCODE_STORAGE,
    OpenCodeHandoffSource,
)

app = typer.Typer(help="Handoff session context to Telegram")

# Backward-compat module-level paths used by older tests / external scripts.
OPENCODE_STORAGE = DEFAULT_OPENCODE_STORAGE
OPENCODE_DB = DEFAULT_OPENCODE_DB

# Engines that have a HandoffSource implementation. Order matters for the
# fallback when ``--engine`` is omitted and ``default_engine`` is not set.
_SUPPORTED_HANDOFF_ENGINES = ("codebuddy", "opencode")


def _build_handoff_source(engine_id: str) -> HandoffSource:
    if engine_id == "opencode":
        return OpenCodeHandoffSource()
    if engine_id == "codebuddy":
        return CodeBuddyHandoffSource()
    raise typer.BadParameter(
        f"handoff is not supported for engine '{engine_id}'. "
        f"Supported engines: {', '.join(_SUPPORTED_HANDOFF_ENGINES)}"
    )


# Re-exported alias for tests that still reference SessionInfo as a dataclass.
SessionInfo = HandoffSessionInfo


def _normalize_project_alias(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _resolve_session_project_root(directory: str | None) -> Path | None:
    if not directory:
        return None
    path = Path(directory).expanduser()
    if not path.exists():
        return None
    return resolve_main_worktree_root(path) or path


def _ensure_handoff_project(
    *,
    project: str | None,
    session_directory: str | None,
    config_path: Path,
) -> tuple[str | None, str | None]:
    project_key = _normalize_project_alias(project)
    if project_key is None:
        return None, None

    try:
        settings, _ = load_settings_if_exists(config_path) or (None, None)
        if settings is None:
            return None, f"项目 {project!r} 未注册，且当前配置不可读取"
        engine_ids = list_backend_ids()
        projects_config = settings.to_projects_config(
            config_path=config_path,
            engine_ids=engine_ids,
            reserved=RESERVED_CHAT_COMMANDS,
        )
    except ConfigError as exc:
        return None, f"项目 {project!r} 未注册，且配置校验失败: {exc}"

    if project_key in projects_config.projects:
        return project_key, None

    if project_key in {engine.lower() for engine in engine_ids}:
        return None, f"项目别名 {project!r} 与引擎 ID 冲突，无法自动注册"
    if project_key in RESERVED_CHAT_COMMANDS:
        return None, f"项目别名 {project!r} 与保留命令冲突，无法自动注册"

    project_root = _resolve_session_project_root(session_directory)
    if project_root is None:
        return None, f"项目 {project!r} 未注册，且无法从会话目录推断仓库根目录"

    config, cfg_path = load_or_init_config(config_path)
    if cfg_path.exists():
        applied = migrate_config(config, config_path=cfg_path)
        if applied:
            write_config(config, cfg_path)

    projects = ensure_table(config, "projects", config_path=cfg_path)
    if project in projects:
        return project_key, None

    entry: dict[str, object] = {
        "path": str(project_root),
        "worktrees_dir": ".worktrees",
    }
    worktree_base = resolve_default_base(project_root)
    if worktree_base:
        entry["worktree_base"] = worktree_base

    projects[project_key] = entry
    write_config(config, cfg_path)
    return project_key, f"auto-registered project {project_key!r}"


def _get_recent_sessions(
    limit: int = 10, *, source: HandoffSource | None = None
) -> list[HandoffSessionInfo]:
    src = source if source is not None else OpenCodeHandoffSource()
    return src.list_sessions(limit=limit)


def _get_session_messages(
    session_id: str, limit: int = 5, *, source: HandoffSource | None = None
) -> list[dict]:
    src = source if source is not None else OpenCodeHandoffSource()
    return src.get_messages(session_id, limit=limit)


def _get_session_model_id(
    session_id: str, *, source: HandoffSource | None = None
) -> str | None:
    src = source if source is not None else OpenCodeHandoffSource()
    return src.get_model_id(session_id)


# --------------------------------------------------------------------------- #
# Legacy SQLite/FS helpers below are kept only because external code/tests may
# still import them. New code should go through HandoffSource instead.
# --------------------------------------------------------------------------- #


def _get_session_messages_sqlite(session_id: str, limit: int = 5) -> list[dict]:
    src = OpenCodeHandoffSource()
    return src._messages_from_sqlite(session_id, limit)


def _get_session_messages_fs(session_id: str, limit: int = 5) -> list[dict]:
    src = OpenCodeHandoffSource()
    return src._messages_from_fs(session_id, limit)


import re


def _escape_markdown(text: str) -> str:
    """转义 Telegram Markdown V1 特殊字符，避免解析错误。"""
    # Markdown V1 特殊字符: _ * ` [
    return re.sub(r"([_*`\[\]])", r"\\\1", text)


def _format_handoff_message(
    session_id: str,
    messages: list[dict],
    project: str | None = None,
) -> str:
    lines = ["📱 **会话接力**", ""]

    if project:
        lines.append(f"📁 项目: `{project}`")
    lines.append(f"🔗 Session: `{session_id}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        role_label = "👤" if role == "user" else "🤖"
        if len(text) > 500:
            text = text[:500]
            # 确保截断不会留下未闭合的反引号
            if text.count("`") % 2 != 0:
                text = text.rsplit("`", 1)[0]
            text += "..."
        text = _escape_markdown(text)
        lines.append(f"{role_label} **{role}**:")
        lines.append(text)
        lines.append("")

    total_len = sum(len(line) for line in lines)
    if total_len > 3500:
        lines = lines[:20]
        lines.append("... (truncated)")

    lines.append("---")
    lines.append("")
    lines.append("💡 直接在此 Topic 发消息即可继续对话")

    return "\n".join(lines)


async def _create_handoff_topic(
    token: str,
    chat_id: int,
    session_id: str,
    project: str | None,
    config_path: Path,
    message: str,
    *,
    engine: str = "opencode",
) -> tuple[int, bool] | None:
    title = f"📱 {project} handoff" if project else "📱 handoff"

    client = TelegramClient(token)
    try:
        result = await client.create_forum_topic(chat_id, title)
        if result is None:
            return None

        thread_id = result.message_thread_id

        state_path = resolve_state_path(config_path)
        store = TopicStateStore(state_path)

        if project:
            context = RunContext(project=project.lower(), branch=None)
            await store.set_context(chat_id, thread_id, context, topic_title=title)
        else:
            await store.set_context(
                chat_id,
                thread_id,
                RunContext(project=None, branch=None),
                topic_title=title,
            )

        resume_token = ResumeToken(engine=engine, value=session_id)
        await store.set_session_resume(chat_id, thread_id, resume_token)

        sent = await _send_message_with_client(
            client,
            chat_id=chat_id,
            message=message,
            thread_id=thread_id,
        )
        return thread_id, sent
    finally:
        await client.close()


async def _send_message_with_client(
    client: TelegramClient,
    chat_id: int,
    message: str,
    thread_id: int | None = None,
) -> bool:
    result = await client.send_message(
        chat_id=chat_id,
        text=message,
        message_thread_id=thread_id,
        parse_mode="Markdown",
    )
    if result is None:
        result = await client.send_message(
            chat_id=chat_id,
            text=message,
            message_thread_id=thread_id,
        )
    if result is None:
        return False
    if (
        thread_id is not None
        and result.message_thread_id is not None
        and result.message_thread_id != thread_id
    ):
        return False
    return True


async def _send_to_telegram(
    token: str,
    chat_id: int,
    message: str,
    thread_id: int | None = None,
) -> bool:
    client = TelegramClient(token)
    try:
        return await _send_message_with_client(
            client,
            chat_id=chat_id,
            message=message,
            thread_id=thread_id,
        )
    finally:
        await client.close()


@app.command()
def send(
    session: str | None = typer.Option(
        None, "--session", "-s", help="Session ID (defaults to latest)"
    ),
    limit: int = typer.Option(3, "--limit", "-n", help="Number of messages to include"),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project name for context"
    ),
    engine: str | None = typer.Option(
        None,
        "--engine",
        "-e",
        help=(
            "Source engine to read sessions from. Defaults to default_engine "
            "in yee88.toml when supported, otherwise opencode. "
            f"Supported: {', '.join(_SUPPORTED_HANDOFF_ENGINES)}"
        ),
    ),
    all_projects: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="List sessions from every project, not just the current cwd.",
    ),
) -> None:
    result = load_settings_if_exists()
    if result is None:
        typer.echo("❌ 未找到 yee88 配置文件", err=True)
        raise typer.Exit(1)

    settings, config_path = result
    telegram_cfg = settings.transports.telegram

    token = telegram_cfg.bot_token
    chat_id = telegram_cfg.chat_id

    if not token or not chat_id:
        typer.echo("❌ Telegram 配置不完整 (需要 bot_token 和 chat_id)", err=True)
        raise typer.Exit(1)

    if not telegram_cfg.topics.enabled:
        typer.echo(
            "❌ Topics 未启用，请先运行: yee88 config set transports.telegram.topics.enabled true",
            err=True,
        )
        raise typer.Exit(1)

    # Resolve handoff engine: explicit --engine wins, then yee88.toml's
    # default_engine when it's something we know how to read, otherwise we
    # fall back to opencode (the historical default) so legacy invocations
    # keep working.
    if engine is not None:
        engine_id = engine
    elif settings.default_engine in _SUPPORTED_HANDOFF_ENGINES:
        engine_id = settings.default_engine
    else:
        engine_id = "opencode"

    if engine_id not in _SUPPORTED_HANDOFF_ENGINES:
        typer.echo(
            f"❌ handoff 暂不支持引擎 '{engine_id}'。"
            f"已支持: {', '.join(_SUPPORTED_HANDOFF_ENGINES)}",
            err=True,
        )
        raise typer.Exit(1)

    source = _build_handoff_source(engine_id)
    engine_label = engine_id.capitalize()

    session_id = session
    session_project = project
    session_directory: str | None = None
    if session_id is None:
        # By default, scope the picker to the cwd the user is invoking from
        # (so `cd ~/Code/wxapplib && yee88 handoff` only shows wxapplib
        # sessions). Pass --all to see sessions across every project.
        scope_cwd = None if all_projects else os.getcwd()
        sessions = source.list_sessions(limit=10, cwd=scope_cwd)

        if not sessions and scope_cwd is not None:
            # Fall back to the global list and warn — clearer than a bare
            # "未找到会话" error when there really *are* sessions, just not
            # in this directory.
            global_sessions = source.list_sessions(limit=10)
            if global_sessions:
                typer.echo(
                    f"ℹ️  当前目录 ({scope_cwd}) 下没有 {engine_label} 会话；"
                    "改为列出全部项目（要重选目录请加 --all 或 cd 到该项目）。",
                    err=True,
                )
                sessions = global_sessions

        if not sessions:
            typer.echo(f"❌ 未找到 {engine_label} 会话", err=True)
            raise typer.Exit(1)

        scope_label = "全部项目" if all_projects or scope_cwd is None else "当前项目"
        typer.echo(
            f"\n📲 会话接力 - 将 {engine_label} 会话发送到 Telegram 继续对话 [{scope_label}]"
        )
        typer.echo("━" * 50)
        typer.echo("\n📋 最近的会话:\n")
        for i, s in enumerate(sessions[:10], 1):
            title_display = s.title[:40] if s.title else s.project_name
            typer.echo(f"  [{i}] {s.updated_str}  {title_display}")
        typer.echo("")

        choice = typer.prompt("选择会话 (1-10)", default="1")
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(sessions):
                typer.echo("❌ 无效选择", err=True)
                raise typer.Exit(1)
        except ValueError:
            typer.echo("❌ 请输入数字", err=True)
            raise typer.Exit(1)

        selected = sessions[idx]
        session_id = selected.id
        session_directory = selected.directory
        if session_project is None:
            session_project = selected.project_name

    if not session_id:
        typer.echo("❌ 会话 ID 为空", err=True)
        raise typer.Exit(1)

    # When the user passed --session explicitly, we still want to backfill
    # session_directory so project auto-registration can find a worktree.
    if session_directory is None:
        session_directory = source.get_session_directory(session_id)

    if session_project:
        ensured_project, note = _ensure_handoff_project(
            project=session_project,
            session_directory=session_directory,
            config_path=config_path,
        )
        if note:
            typer.echo(f"ℹ️  {note}")
        if ensured_project is None:
            typer.echo(
                f"⚠️  项目 {session_project!r} 不可用于话题上下文，将以无项目上下文继续",
                err=True,
            )
            session_project = None
        else:
            session_project = ensured_project

    typer.echo(f"📖 读取会话 {session_id[:20]}...")

    messages = source.get_messages(session_id, limit=limit)
    if not messages:
        typer.echo("❌ 无法读取会话消息", err=True)
        raise typer.Exit(1)

    async def do_handoff() -> tuple[bool, int | None, bool]:
        project_name = session_project
        handoff_msg = _format_handoff_message(
            session_id=session_id,
            messages=messages,
            project=session_project,
        )
        context = (
            RunContext(project=project_name.lower(), branch=None)
            if project_name
            else None
        )

        # 先查找已有的同项目 topic，避免重复创建
        state_path = resolve_state_path(config_path)
        store = TopicStateStore(state_path)
        existing_thread_id = (
            await store.find_thread_for_context(chat_id, context)
            if context is not None
            else None
        )

        thread_id: int | None = None
        reused = False
        created_and_sent = False

        if existing_thread_id is not None:
            # 复用已有 topic
            resume_token = ResumeToken(engine=engine_id, value=session_id)
            await store.set_session_resume(chat_id, existing_thread_id, resume_token)
            thread_id = existing_thread_id
            reused = True

        if thread_id is None:
            created = await _create_handoff_topic(
                token=token,
                chat_id=chat_id,
                session_id=session_id,
                project=project_name,
                config_path=config_path,
                message=handoff_msg,
                engine=engine_id,
            )
            if created is not None:
                thread_id, created_and_sent = created
            reused = False

        if thread_id is None:
            return False, None, False

        # 设置 topic 默认引擎，确保 Telegram 端继续对话时使用正确引擎
        await store.set_default_engine(chat_id, thread_id, engine_id)

        # 从原 session 提取模型 ID，设置为 topic 的 engine override
        model_id = source.get_model_id(session_id)
        if model_id:
            override = EngineOverrides(model=model_id)
            await store.set_engine_override(chat_id, thread_id, engine_id, override)

        success = created_and_sent
        if not created_and_sent:
            success = await _send_to_telegram(
                token=token,
                chat_id=chat_id,
                message=handoff_msg,
                thread_id=thread_id,
            )

        # 发送失败：清理并重建一次 topic 重试
        if not success:
            await store.delete_thread(chat_id, thread_id)
            created = await _create_handoff_topic(
                token=token,
                chat_id=chat_id,
                session_id=session_id,
                project=project_name,
                config_path=config_path,
                message=handoff_msg,
                engine=engine_id,
            )
            if created is None:
                return False, None, False
            thread_id, success = created
            reused = False
            await store.set_default_engine(chat_id, thread_id, engine_id)
            if model_id:
                override = EngineOverrides(model=model_id)
                await store.set_engine_override(
                    chat_id, thread_id, engine_id, override
                )

        return success, thread_id, reused

    success, thread_id, reused = anyio.run(do_handoff)

    if success:
        if reused:
            typer.echo("✅ 已发送到已有 Topic！")
        else:
            typer.echo("✅ 已创建新 Topic 并发送！")
        typer.echo(f"   Session: {session_id}")
        typer.echo(f"   Project: {session_project or '<none>'}")
        typer.echo(f"   Topic ID: {thread_id}")
        typer.echo(f"   消息数: {limit}")
    else:
        typer.echo("❌ 发送失败", err=True)
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Handoff session context to Telegram for mobile continuation."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(
            send,
            session=None,
            limit=3,
            project=None,
            engine=None,
            all_projects=False,
        )
