"""CLI command to send a file to a Telegram chat.

Automatically detects the file type (image vs document) and uses the
appropriate Telegram API method (sendPhoto vs sendDocument).
"""

from __future__ import annotations

import mimetypes
import os
from functools import partial
from pathlib import Path

import anyio
import typer

from ..logging import setup_logging
from ..settings import load_settings_if_exists
from ..telegram.client import TelegramClient


def _is_image_mime(mime_type: str) -> bool:
    """Return True if the MIME type is a sendable image type for Telegram."""
    return mime_type.startswith("image/") and mime_type not in {
        "image/svg+xml",
        "image/tiff",
    }


async def _send_file(
    *,
    token: str,
    chat_id: int,
    file_path: Path,
    thread_id: int | None,
    caption: str | None,
) -> bool:
    """Send a file to a Telegram chat, auto-detecting image vs document."""
    if not file_path.exists():
        typer.echo(f"❌ 文件不存在: {file_path}", err=True)
        return False
    if not file_path.is_file():
        typer.echo(f"❌ 不是文件: {file_path}", err=True)
        return False

    content = file_path.read_bytes()
    filename = file_path.name

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    client = TelegramClient(token)
    try:
        if _is_image_mime(mime_type):
            result = await client.send_photo(
                chat_id=chat_id,
                filename=filename,
                content=content,
                message_thread_id=thread_id,
                caption=caption,
            )
        else:
            result = await client.send_document(
                chat_id=chat_id,
                filename=filename,
                content=content,
                message_thread_id=thread_id,
                caption=caption,
            )
        return result is not None
    finally:
        await client.close()


def send_file(
    file_path: str = typer.Argument(..., help="Path to the file to send."),
    chat_id: int | None = typer.Option(
        None,
        "--chat-id",
        "-c",
        help="Telegram chat ID. Defaults to config value.",
        envvar="YEE88_CHAT_ID",
    ),
    thread_id: int | None = typer.Option(
        None,
        "--thread-id",
        "-t",
        help="Telegram message thread ID (topic).",
        envvar="YEE88_THREAD_ID",
    ),
    caption: str | None = typer.Option(
        None,
        "--caption",
        help="Optional caption for the file.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Telegram bot token. Defaults to config value.",
    ),
) -> None:
    """Send a file to a Telegram chat (auto-detects image vs document)."""
    setup_logging(debug=False, cache_logger_on_first_use=False)

    # Resolve token and chat_id from config if not provided
    if token is None or chat_id is None:
        result = load_settings_if_exists()
        if result is not None:
            settings, _ = result
            tg = settings.transports.telegram
            if token is None:
                token = tg.bot_token or None
            if chat_id is None:
                chat_id = tg.chat_id

    if not token:
        typer.echo("❌ Telegram bot token 未配置", err=True)
        raise typer.Exit(1)
    if chat_id is None:
        typer.echo("❌ chat_id 未指定 (使用 --chat-id 或配置文件)", err=True)
        raise typer.Exit(1)

    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path

    ok = anyio.run(
        partial(
            _send_file,
            token=token,
            chat_id=chat_id,
            file_path=path,
            thread_id=thread_id,
            caption=caption,
        )
    )
    if not ok:
        raise typer.Exit(1)
