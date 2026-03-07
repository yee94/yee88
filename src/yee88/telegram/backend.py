from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Literal

import anyio

from ..backends import EngineBackend
from ..logging import get_logger
from ..runner_bridge import ExecBridgeConfig
from ..settings import TelegramTopicsSettings, TelegramTransportSettings, load_settings_if_exists
from ..transport_runtime import TransportRuntime
from ..transports import SetupResult, TransportBackend
from .bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    run_main_loop,
)
from .client import TelegramClient
from .onboarding import check_setup, interactive_setup
from .topics import _resolve_topics_scope_raw

logger = get_logger(__name__)


def _expect_transport_settings(transport_config: object) -> TelegramTransportSettings:
    if isinstance(transport_config, TelegramTransportSettings):
        return transport_config
    raise TypeError("transport_config must be TelegramTransportSettings")


def _build_startup_message(
    runtime: TransportRuntime,
    *,
    startup_pwd: str,
    chat_id: int,
    session_mode: Literal["stateless", "chat"],
    show_resume_line: bool,
    topics: TelegramTopicsSettings,
) -> str:
    # Collect engine warnings so the user knows if something is broken.
    missing_engines = list(runtime.missing_engine_ids())
    misconfigured_engines = list(runtime.engine_ids_with_status("bad_config"))
    failed_engines = list(runtime.engine_ids_with_status("load_error"))

    warnings: list[str] = []
    if missing_engines:
        warnings.append(f"not installed: {', '.join(missing_engines)}")
    if misconfigured_engines:
        warnings.append(f"misconfigured: {', '.join(misconfigured_engines)}")
    if failed_engines:
        warnings.append(f"failed to load: {', '.join(failed_engines)}")

    greetings = [
        "老板，我醒了，有什么吩咐? 🫡",
        "来了来了，随时待命 💪",
        "报到! 说吧，今天干点啥? 🤓",
        "准备就绪，等你发话 🎯",
        "上线了，老板尽管吩咐 🐶",
        "打卡成功，开始营业~ 🏪",
        "就位! 有活儿尽管派 🔥",
        "嗨老板，想我了没? 😏",
        "又是为老板打工的一天 🥱",
        "醒了醒了，别催~ 😤",
    ]
    lines = [random.choice(greetings)]
    if warnings:
        lines.append("")
        lines.append(f"(engine warnings: {'; '.join(warnings)})")
    return "\n".join(lines)


class TelegramBackend(TransportBackend):
    id = "telegram"
    description = "Telegram bot"

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        return check_setup(engine_backend, transport_override=transport_override)

    async def interactive_setup(self, *, force: bool) -> bool:
        return await interactive_setup(force=force)

    def lock_token(self, *, transport_config: object, _config_path: Path) -> str | None:
        settings = _expect_transport_settings(transport_config)
        return settings.bot_token

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        settings = _expect_transport_settings(transport_config)
        token = settings.bot_token
        chat_id = settings.chat_id
        startup_msg = _build_startup_message(
            runtime,
            startup_pwd=os.getcwd(),
            chat_id=chat_id,
            session_mode=settings.session_mode,
            show_resume_line=settings.show_resume_line,
            topics=settings.topics,
        )
        bot = TelegramClient(token)
        transport = TelegramTransport(bot)
        presenter = TelegramPresenter(message_overflow=settings.message_overflow)
        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )
        cfg = TelegramBridgeConfig(
            bot=bot,
            runtime=runtime,
            chat_id=chat_id,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            session_mode=settings.session_mode,
            show_resume_line=settings.show_resume_line,
            voice_transcription=settings.voice_transcription,
            voice_max_bytes=int(settings.voice_max_bytes),
            voice_transcription_model=settings.voice_transcription_model,
            voice_transcription_base_url=settings.voice_transcription_base_url,
            voice_transcription_api_key=settings.voice_transcription_api_key,
            forward_coalesce_s=settings.forward_coalesce_s,
            media_group_debounce_s=settings.media_group_debounce_s,
            allowed_user_ids=tuple(settings.allowed_user_ids),
            topics=settings.topics,
            files=settings.files,
            cron=settings.cron,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                watch_config=runtime.watch_config,
                default_engine_override=default_engine_override,
                transport_id=self.id,
                transport_config=settings,
            )

        anyio.run(run_loop)


telegram_backend = TelegramBackend()
BACKEND = telegram_backend
