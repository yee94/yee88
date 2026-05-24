from __future__ import annotations

from pathlib import Path
import tomllib
from typing import cast

import pytest

from yee88.cli import handoff
from yee88.telegram.client import TelegramClient
from yee88.telegram.api_schemas import Chat, ForumTopic, Message
from yee88.telegram.topic_state import TopicStateStore, resolve_state_path


def _write_min_config(path: Path) -> None:
    path.write_text(
        '[transports.telegram]\nbot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )


def test_ensure_handoff_project_auto_registers_missing_project(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "yee88.toml"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _write_min_config(config_path)

    monkeypatch.setattr(handoff, "list_backend_ids", lambda: ["codex"])
    monkeypatch.setattr(
        handoff,
        "resolve_main_worktree_root",
        lambda path: path if path == repo_path else None,
    )
    monkeypatch.setattr(handoff, "resolve_default_base", lambda _path: "main")

    project, note = handoff._ensure_handoff_project(
        project="lws",
        session_directory=str(repo_path),
        config_path=config_path,
    )

    assert project == "lws"
    assert note == "auto-registered project 'lws'"

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["projects"]["lws"]["path"] == str(repo_path)
    assert data["projects"]["lws"]["worktrees_dir"] == ".worktrees"
    assert data["projects"]["lws"]["worktree_base"] == "main"


def test_ensure_handoff_project_rejects_conflicting_alias(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "yee88.toml"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _write_min_config(config_path)

    monkeypatch.setattr(handoff, "list_backend_ids", lambda: ["codex", "lws"])

    project, note = handoff._ensure_handoff_project(
        project="lws",
        session_directory=str(repo_path),
        config_path=config_path,
    )

    assert project is None
    assert note == "项目别名 'lws' 与引擎 ID 冲突，无法自动注册"

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "projects" not in data


@pytest.mark.anyio
async def test_create_handoff_topic_sends_first_message_into_new_thread(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "yee88.toml"
    sent_calls: list[dict[str, object]] = []

    class _FakeClient:
        async def create_forum_topic(
            self, chat_id: int, name: str
        ) -> ForumTopic | None:
            assert chat_id == 123
            assert name == "📱 lws handoff"
            return ForumTopic(message_thread_id=77)

        async def send_message(
            self,
            chat_id: int,
            text: str,
            reply_to_message_id: int | None = None,
            disable_notification: bool | None = False,
            message_thread_id: int | None = None,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            replace_message_id: int | None = None,
        ) -> Message | None:
            _ = reply_to_message_id, disable_notification, entities, reply_markup
            _ = replace_message_id
            sent_calls.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "message_thread_id": message_thread_id,
                    "parse_mode": parse_mode,
                }
            )
            return Message(
                message_id=1,
                chat=Chat(id=chat_id, type="supergroup"),
                message_thread_id=message_thread_id,
            )

        async def close(self) -> None:
            return None

    handoff.TelegramClient = lambda _token: _FakeClient()  # type: ignore[assignment]

    created = await handoff._create_handoff_topic(
        token="token",
        chat_id=123,
        session_id="sess-1",
        project="lws",
        config_path=config_path,
        message="hello",
    )

    assert created == (77, True)
    assert sent_calls == [
        {
            "chat_id": 123,
            "text": "hello",
            "message_thread_id": 77,
            "parse_mode": "Markdown",
        }
    ]

    store = TopicStateStore(resolve_state_path(config_path))
    snapshot = await store.get_thread(123, 77)
    assert snapshot is not None
    assert snapshot.context is not None
    assert snapshot.context.project == "lws"


@pytest.mark.anyio
async def test_send_message_with_client_rejects_thread_mismatch() -> None:
    class _MismatchClient:
        async def send_message(
            self,
            chat_id: int,
            text: str,
            reply_to_message_id: int | None = None,
            disable_notification: bool | None = False,
            message_thread_id: int | None = None,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            replace_message_id: int | None = None,
        ) -> Message | None:
            _ = (
                chat_id,
                text,
                reply_to_message_id,
                disable_notification,
                message_thread_id,
                entities,
                parse_mode,
                reply_markup,
                replace_message_id,
            )
            return Message(
                message_id=1,
                chat=Chat(id=123, type="supergroup"),
                message_thread_id=1,
            )

    ok = await handoff._send_message_with_client(
        cast(TelegramClient, _MismatchClient()),
        chat_id=123,
        message="hello",
        thread_id=77,
    )

    assert ok is False


@pytest.mark.anyio
async def test_create_handoff_topic_writes_resume_token_with_engine(
    tmp_path: Path,
) -> None:
    """When engine="codebuddy" is passed, the stored ResumeToken must use it."""
    config_path = tmp_path / "yee88.toml"

    class _FakeClient:
        async def create_forum_topic(
            self, chat_id: int, name: str
        ) -> ForumTopic | None:
            return ForumTopic(message_thread_id=88)

        async def send_message(
            self,
            chat_id: int,
            text: str,
            reply_to_message_id: int | None = None,
            disable_notification: bool | None = False,
            message_thread_id: int | None = None,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            replace_message_id: int | None = None,
        ) -> Message | None:
            _ = (
                reply_to_message_id,
                disable_notification,
                entities,
                parse_mode,
                reply_markup,
                replace_message_id,
            )
            return Message(
                message_id=1,
                chat=Chat(id=chat_id, type="supergroup"),
                message_thread_id=message_thread_id,
            )

        async def close(self) -> None:
            return None

    handoff.TelegramClient = lambda _token: _FakeClient()  # type: ignore[assignment]

    created = await handoff._create_handoff_topic(
        token="token",
        chat_id=999,
        session_id="cb-sess-42",
        project=None,
        config_path=config_path,
        message="hi",
        engine="codebuddy",
    )

    assert created is not None
    thread_id = created[0]

    store = TopicStateStore(resolve_state_path(config_path))
    snapshot = await store.get_thread(999, thread_id)
    assert snapshot is not None
    # The stored session must be keyed by the engine we passed.
    assert snapshot.sessions.get("codebuddy") == "cb-sess-42"
    # And no opencode entry should have been written.
    assert "opencode" not in snapshot.sessions


def test_build_handoff_source_returns_correct_implementations() -> None:
    from yee88.cli.handoff_sources.codebuddy import CodeBuddyHandoffSource
    from yee88.cli.handoff_sources.opencode import OpenCodeHandoffSource

    assert isinstance(handoff._build_handoff_source("opencode"), OpenCodeHandoffSource)
    assert isinstance(
        handoff._build_handoff_source("codebuddy"), CodeBuddyHandoffSource
    )


def test_build_handoff_source_rejects_unsupported_engine() -> None:
    import typer

    with pytest.raises(typer.BadParameter):
        handoff._build_handoff_source("claude")


def test_supported_handoff_engines_contains_codebuddy_and_opencode() -> None:
    assert "codebuddy" in handoff._SUPPORTED_HANDOFF_ENGINES
    assert "opencode" in handoff._SUPPORTED_HANDOFF_ENGINES


def test_handoff_no_subcommand_does_not_leak_optioninfo_into_engine_arg(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression: ``yee88 handoff`` (no ``send`` subcommand) used to forward
    a Typer OptionInfo sentinel as the engine string, producing the error
    "handoff 暂不支持引擎 '<typer.models.OptionInfo object at ...>'".
    The callback must explicitly pass engine=None so ``send`` can resolve it
    from settings.default_engine.
    """
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="codebuddy"),
    )

    captured: dict[str, str] = {}
    real_build = handoff._build_handoff_source

    def spy(engine_id: str):  # type: ignore[no-untyped-def]
        captured["engine_id"] = engine_id
        return real_build(engine_id)

    monkeypatch.setattr("yee88.cli.handoff._build_handoff_source", spy)

    runner = CliRunner()
    result = runner.invoke(handoff.app, [])  # No subcommand at all

    assert "OptionInfo" not in result.output, result.output
    assert captured.get("engine_id") == "codebuddy", result.output


def test_send_cli_dispatches_to_codebuddy_source(
    monkeypatch, tmp_path: Path
) -> None:
    """End-to-end: ``yee88 handoff send --engine codebuddy`` must use the
    CodeBuddyHandoffSource (not the OpenCode one) when listing sessions.
    """
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path),
    )

    captured: dict[str, str] = {}
    real_build = handoff._build_handoff_source

    def spy(engine_id: str):  # type: ignore[no-untyped-def]
        captured["engine_id"] = engine_id
        return real_build(engine_id)

    monkeypatch.setattr("yee88.cli.handoff._build_handoff_source", spy)

    runner = CliRunner()
    # No sessions on disk → command exits with "未找到 ... 会话" error,
    # but only after the source has been resolved.
    result = runner.invoke(handoff.app, ["send", "--engine", "codebuddy"])

    assert result.exit_code != 0
    assert captured.get("engine_id") == "codebuddy"


def test_send_cli_uses_default_engine_when_no_flag(
    monkeypatch, tmp_path: Path
) -> None:
    """Without --engine, the CLI must read default_engine from settings."""
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="codebuddy"),
    )

    captured: dict[str, str] = {}

    def spy(engine_id: str):  # type: ignore[no-untyped-def]
        captured["engine_id"] = engine_id
        return handoff._build_handoff_source.__wrapped__(engine_id)  # type: ignore[attr-defined]

    real_build = handoff._build_handoff_source

    def spy2(engine_id: str):  # type: ignore[no-untyped-def]
        captured["engine_id"] = engine_id
        return real_build(engine_id)

    monkeypatch.setattr("yee88.cli.handoff._build_handoff_source", spy2)

    runner = CliRunner()
    result = runner.invoke(handoff.app, ["send"])

    assert result.exit_code != 0
    assert captured["engine_id"] == "codebuddy"


def test_send_cli_falls_back_to_opencode_for_unsupported_default_engine(
    monkeypatch, tmp_path: Path
) -> None:
    """If default_engine is set to e.g. 'claude' (no handoff source), the CLI
    falls back to opencode for backward compatibility.
    """
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="claude"),
    )

    captured: dict[str, str] = {}
    real_build = handoff._build_handoff_source

    def spy(engine_id: str):  # type: ignore[no-untyped-def]
        captured["engine_id"] = engine_id
        return real_build(engine_id)

    monkeypatch.setattr("yee88.cli.handoff._build_handoff_source", spy)

    runner = CliRunner()
    result = runner.invoke(handoff.app, ["send"])

    assert result.exit_code != 0
    assert captured["engine_id"] == "opencode"


def test_send_cli_rejects_unsupported_engine_flag(
    monkeypatch, tmp_path: Path
) -> None:
    """--engine claude (or anything not in _SUPPORTED_HANDOFF_ENGINES) must
    error out with a clear message instead of silently using opencode.
    """
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path),
    )

    runner = CliRunner()
    result = runner.invoke(handoff.app, ["send", "--engine", "claude"])

    assert result.exit_code != 0
    assert "claude" in result.output


def _fake_settings(config_path: Path, *, default_engine: str = "codebuddy"):
    """Minimal stand-in for load_settings_if_exists in CLI tests."""

    class _Topics:
        enabled = True

    class _Telegram:
        bot_token = "token"
        chat_id = 123
        topics = _Topics()

    class _Transports:
        telegram = _Telegram()

    class _Settings:
        transports = _Transports()

    s = _Settings()
    s.default_engine = default_engine
    return s, config_path


def test_handoff_default_filters_sessions_by_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    """``yee88 handoff`` from /Code/wxapplib must NOT show yee88 sessions."""
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="codebuddy"),
    )
    monkeypatch.chdir(tmp_path)

    calls: list[str | None] = []

    class _StubSource:
        engine_id = "codebuddy"

        def list_sessions(self, limit: int = 10, *, cwd: str | None = None):
            calls.append(cwd)
            return []

        def get_messages(self, *_args, **_kw):
            return []

        def get_model_id(self, *_args, **_kw):
            return None

        def get_session_directory(self, *_args, **_kw):
            return None

    monkeypatch.setattr(
        "yee88.cli.handoff._build_handoff_source", lambda _e: _StubSource()
    )

    runner = CliRunner()
    runner.invoke(handoff.app, [])

    # The very first list call must scope to the current cwd.
    assert calls, "list_sessions was never called"
    assert calls[0] == str(tmp_path)


def test_handoff_all_flag_disables_cwd_filter(
    monkeypatch, tmp_path: Path
) -> None:
    """``--all`` must request the global list (cwd=None)."""
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="codebuddy"),
    )
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    class _StubSource:
        engine_id = "codebuddy"

        def list_sessions(self, limit: int = 10, *, cwd: str | None = None):
            captured.setdefault("calls", []).append(cwd)  # type: ignore[union-attr]
            return []

        def get_messages(self, *_args, **_kw):
            return []

        def get_model_id(self, *_args, **_kw):
            return None

        def get_session_directory(self, *_args, **_kw):
            return None

    monkeypatch.setattr(
        "yee88.cli.handoff._build_handoff_source", lambda _e: _StubSource()
    )

    runner = CliRunner()
    runner.invoke(handoff.app, ["send", "--all"])

    # With --all, the very first list call must use cwd=None (no filter).
    assert captured["calls"][0] is None  # type: ignore[index]


def test_handoff_falls_back_to_global_when_cwd_has_no_sessions(
    monkeypatch, tmp_path: Path
) -> None:
    """When the current directory has zero sessions but other projects do,
    the CLI silently widens the search and warns the user, rather than
    bailing out with '未找到会话'.
    """
    from typer.testing import CliRunner

    config_path = tmp_path / "yee88.toml"
    monkeypatch.setattr(
        "yee88.cli.handoff.load_settings_if_exists",
        lambda: _fake_settings(config_path, default_engine="codebuddy"),
    )
    monkeypatch.chdir(tmp_path)

    from yee88.cli.handoff_sources import HandoffSessionInfo

    class _StubSource:
        engine_id = "codebuddy"
        calls: list[str | None] = []

        def list_sessions(self, limit: int = 10, *, cwd: str | None = None):
            self.calls.append(cwd)
            if cwd is not None:
                return []  # current cwd has nothing
            return [
                HandoffSessionInfo(
                    id="elsewhere",
                    directory="/other/proj",
                    updated=1.0,
                    title="t",
                )
            ]

        def get_messages(self, *_args, **_kw):
            return []

        def get_model_id(self, *_args, **_kw):
            return None

        def get_session_directory(self, *_args, **_kw):
            return "/other/proj"

    stub = _StubSource()
    monkeypatch.setattr(
        "yee88.cli.handoff._build_handoff_source", lambda _e: stub
    )

    runner = CliRunner()
    # Provide "1" on stdin to pick the first session, then we'll let it fail
    # later when it tries to actually send (no Telegram). We only care that
    # the picker showed sessions instead of bailing out.
    result = runner.invoke(handoff.app, ["send"], input="1\n")

    # Two list calls: first scoped to cwd, then global fallback.
    assert stub.calls[0] == str(tmp_path)
    assert stub.calls[1] is None
    assert "未找到 Codebuddy 会话" not in result.output
    assert "改为列出全部项目" in result.output
