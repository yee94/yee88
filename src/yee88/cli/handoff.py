from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
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

app = typer.Typer(help="Handoff session context to Telegram")

OPENCODE_STORAGE = Path.home() / ".local" / "share" / "opencode" / "storage"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@dataclass
class SessionInfo:
    id: str
    directory: str
    updated: float
    title: str

    @property
    def project_name(self) -> str:
        return Path(self.directory).name if self.directory else "unknown"

    @property
    def updated_str(self) -> str:
        return datetime.fromtimestamp(self.updated / 1000).strftime("%m-%d %H:%M")


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


def _get_recent_sessions(limit: int = 10) -> list[SessionInfo]:
    try:
        result = subprocess.run(
            ["opencode", "session", "list", "--format", "json", "-n", str(limit)],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return [
            SessionInfo(
                id=s.get("id", ""),
                directory=s.get("directory", ""),
                updated=s.get("updated", 0),
                title=s.get("title", ""),
            )
            for s in data
        ]
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []


def _get_session_messages(session_id: str, limit: int = 5) -> list[dict]:
    # 优先从 SQLite 数据库读取（新版 OpenCode 格式）
    if OPENCODE_DB.exists():
        result = _get_session_messages_sqlite(session_id, limit)
        if result:
            return result

    # 回退到旧版文件系统格式
    return _get_session_messages_fs(session_id, limit)


def _get_session_model_id(session_id: str) -> str | None:
    """从 OpenCode session 最近的 assistant 消息中提取完整模型 ID (providerID/modelID)。"""
    if not OPENCODE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(OPENCODE_DB))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # 取最近一条 assistant 消息的 modelID 和 providerID
        cursor.execute(
            "SELECT data FROM message "
            "WHERE session_id = ? "
            "ORDER BY time_created DESC LIMIT 20",
            (session_id,),
        )
        for row in cursor.fetchall():
            msg_data = json.loads(row["data"])
            model_id = msg_data.get("modelID")
            if model_id and msg_data.get("role") == "assistant":
                provider_id = msg_data.get("providerID")
                conn.close()
                # 拼接完整模型 ID: providerID/modelID
                if provider_id:
                    return f"{provider_id}/{model_id}"
                return model_id
        conn.close()
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        pass
    return None


def _get_session_messages_sqlite(session_id: str, limit: int = 5) -> list[dict]:
    try:
        conn = sqlite3.connect(str(OPENCODE_DB))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 取最近 limit 条 user/assistant 消息
        cursor.execute(
            "SELECT id, data FROM message "
            "WHERE session_id = ? "
            "ORDER BY time_created DESC LIMIT ?",
            (session_id, limit),
        )
        rows = cursor.fetchall()
        rows.reverse()  # 按时间正序

        result = []
        for row in rows:
            msg_data = json.loads(row["data"])
            role = msg_data.get("role", "unknown")
            msg_id = row["id"]

            # 从 part 表读取文本内容
            cursor.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created ASC",
                (msg_id,),
            )
            for part_row in cursor.fetchall():
                part_data = json.loads(part_row["data"])
                if part_data.get("type") == "text":
                    text = part_data.get("text", "")
                    result.append({"role": role, "text": text})
                    break

        conn.close()
        return result
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return []


def _get_session_messages_fs(session_id: str, limit: int = 5) -> list[dict]:
    """旧版文件系统格式读取（兼容）"""
    message_dir = OPENCODE_STORAGE / "message" / session_id
    if not message_dir.exists():
        return []

    messages: list[tuple[int, str, str]] = []
    for msg_file in message_dir.glob("msg_*.json"):
        try:
            data = json.loads(msg_file.read_text())
            created = data.get("time", {}).get("created", 0)
            role = data.get("role", "unknown")
            msg_id = data.get("id", "")
            messages.append((created, role, msg_id))
        except (json.JSONDecodeError, OSError):
            continue

    messages.sort(key=lambda x: x[0], reverse=True)
    messages = messages[:limit]
    messages.reverse()

    result = []
    for _, role, msg_id in messages:
        part_dir = OPENCODE_STORAGE / "part" / msg_id
        if not part_dir.exists():
            continue
        for part_file in part_dir.glob("prt_*.json"):
            try:
                part_data = json.loads(part_file.read_text())
                if part_data.get("type") == "text":
                    text = part_data.get("text", "")
                    if text.startswith('"') and text.endswith('"\n'):
                        text = json.loads(text.rstrip("\n"))
                    result.append({"role": role, "text": text})
                    break
            except (json.JSONDecodeError, OSError):
                continue

    return result


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
) -> int | None:
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

        resume_token = ResumeToken(engine="opencode", value=session_id)
        await store.set_session_resume(chat_id, thread_id, resume_token)

        return thread_id
    finally:
        await client.close()


async def _send_to_telegram(
    token: str,
    chat_id: int,
    message: str,
    thread_id: int | None = None,
) -> bool:
    client = TelegramClient(token)
    try:
        # 先尝试 Markdown 格式发送
        result = await client.send_message(
            chat_id=chat_id,
            text=message,
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        if result is not None:
            return True
        # Markdown 解析失败时降级为纯文本
        result = await client.send_message(
            chat_id=chat_id,
            text=message,
            message_thread_id=thread_id,
        )
        return result is not None
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

    session_id = session
    session_project = project
    session_directory: str | None = None
    if session_id is None:
        sessions = _get_recent_sessions(limit=10)
        if not sessions:
            typer.echo("❌ 未找到 OpenCode 会话", err=True)
            raise typer.Exit(1)

        typer.echo("\n📲 会话接力 - 将电脑端会话发送到 Telegram 继续对话")
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

    messages = _get_session_messages(session_id, limit=limit)
    if not messages:
        typer.echo("❌ 无法读取会话消息", err=True)
        raise typer.Exit(1)

    async def do_handoff() -> tuple[bool, int | None, bool]:
        project_name = session_project
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

        if existing_thread_id is not None:
            # 尝试复用已有 topic，先验证它在 Telegram 端是否还存在
            resume_token = ResumeToken(engine="opencode", value=session_id)
            await store.set_session_resume(chat_id, existing_thread_id, resume_token)
            thread_id = existing_thread_id
            reused = True

        if thread_id is None:
            # 创建新 topic
            thread_id = await _create_handoff_topic(
                token=token,
                chat_id=chat_id,
                session_id=session_id,
                project=project_name,
                config_path=config_path,
            )
            reused = False

        if thread_id is None:
            return False, None, False

        # 设置 topic 默认引擎为 opencode，确保 Telegram 端继续对话时使用正确引擎
        await store.set_default_engine(chat_id, thread_id, "opencode")

        # 从原 session 提取模型 ID，设置为 topic 的 engine override
        model_id = _get_session_model_id(session_id)
        if model_id:
            override = EngineOverrides(model=model_id)
            await store.set_engine_override(chat_id, thread_id, "opencode", override)

        handoff_msg = _format_handoff_message(
            session_id=session_id,
            messages=messages,
            project=session_project,
        )

        success = await _send_to_telegram(
            token=token,
            chat_id=chat_id,
            message=handoff_msg,
            thread_id=thread_id,
        )

        # 如果复用的 topic 发送失败（可能已被删除），清理 state 并创建新 topic 重试
        if not success and reused:
            await store.delete_thread(chat_id, thread_id)
            thread_id = await _create_handoff_topic(
                token=token,
                chat_id=chat_id,
                session_id=session_id,
                project=project_name,
                config_path=config_path,
            )
            if thread_id is None:
                return False, None, False
            reused = False
            await store.set_default_engine(chat_id, thread_id, "opencode")
            if model_id:
                override = EngineOverrides(model=model_id)
                await store.set_engine_override(
                    chat_id, thread_id, "opencode", override
                )
            success = await _send_to_telegram(
                token=token,
                chat_id=chat_id,
                message=handoff_msg,
                thread_id=thread_id,
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
        ctx.invoke(send, session=None, limit=3, project=None)
