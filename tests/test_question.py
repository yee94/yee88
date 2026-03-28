"""Tests for the question tool support."""

from __future__ import annotations

import json

import pytest

from yee88.model import ActionEvent, Action, ResumeToken
from yee88.runners import tool_actions
from yee88.runners.opencode import (
    OpenCodeStreamState,
    ENGINE,
    translate_opencode_event,
)
from yee88.schemas import opencode as opencode_schema
from yee88.telegram.commands.question import (
    QUESTION_CALLBACK_PREFIX,
    build_question_disabled_notice,
    build_question_disabled_reply,
    build_question_callback_data,
    build_question_keyboard,
    format_question_answer,
    format_question_message,
    format_question_text_plain,
    parse_question_callback_data,
)
from yee88.telegram.types import TelegramCallbackQuery
from yee88.utils.paths import reset_run_base_dir, set_run_base_dir


# ---------------------------------------------------------------------------
# tool_actions: question tool recognition
# ---------------------------------------------------------------------------


class TestToolActionsQuestion:
    def test_question_tool_recognized(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "question",
                {"questions": [{"question": "Pick a color", "header": "Color"}]},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert title == "Color"

    def test_question_tool_with_long_header_truncated(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "question",
                {"questions": [{"question": "x", "header": "A" * 100}]},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert len(title) == 60

    def test_question_tool_falls_back_to_question_text(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "question",
                {"questions": [{"question": "What framework?"}]},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert title == "What framework?"

    def test_question_tool_empty_questions(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "question",
                {"questions": []},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert title == "ask user"

    def test_question_tool_no_questions_key(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "question",
                {},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert title == "ask user"

    def test_askuserquestion_also_recognized(self) -> None:
        token = set_run_base_dir(None)
        try:
            kind, title = tool_actions.tool_kind_and_title(
                "askuserquestion",
                {},
                path_keys=("path",),
            )
        finally:
            reset_run_base_dir(token)
        assert kind == "question"
        assert title == "ask user"


# ---------------------------------------------------------------------------
# opencode runner: question tool_use event translation
# ---------------------------------------------------------------------------


def _decode_event(payload: dict) -> opencode_schema.OpenCodeEvent:
    return opencode_schema.decode_event(json.dumps(payload).encode("utf-8"))


class TestOpenCodeQuestionEvent:
    def _make_question_tool_use(
        self, *, status: str = "pending"
    ) -> opencode_schema.OpenCodeEvent:
        return _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_q1",
                "part": {
                    "id": "prt_q1",
                    "callID": "call_q1",
                    "tool": "question",
                    "state": {
                        "status": status,
                        "input": {
                            "questions": [
                                {
                                    "question": "Which framework?",
                                    "header": "Framework",
                                    "options": [
                                        {"label": "React", "description": "UI lib"},
                                        {"label": "Vue", "description": "Progressive"},
                                    ],
                                    "multiple": False,
                                    "custom": True,
                                }
                            ]
                        },
                    },
                },
            }
        )

    def test_question_pending_emits_started_action(self) -> None:
        state = OpenCodeStreamState()
        state.session_id = "ses_q1"
        state.emitted_started = True

        events = translate_opencode_event(
            self._make_question_tool_use(status="pending"),
            title="opencode",
            state=state,
        )

        assert len(events) == 1
        evt = events[0]
        assert isinstance(evt, ActionEvent)
        assert evt.phase == "started"
        assert evt.action.kind == "question"
        assert evt.action.title == "Framework"
        assert "questions" in evt.action.detail
        questions = evt.action.detail["questions"]
        assert len(questions) == 1
        assert questions[0]["question"] == "Which framework?"
        assert len(questions[0]["options"]) == 2

    def test_question_pending_stored_in_pending_actions(self) -> None:
        state = OpenCodeStreamState()
        state.session_id = "ses_q1"
        state.emitted_started = True

        translate_opencode_event(
            self._make_question_tool_use(status="pending"),
            title="opencode",
            state=state,
        )

        assert "call_q1" in state.pending_actions
        assert state.pending_actions["call_q1"].kind == "question"

    def test_question_completed_emits_completed_action(self) -> None:
        state = OpenCodeStreamState()
        state.session_id = "ses_q1"
        state.emitted_started = True

        events = translate_opencode_event(
            self._make_question_tool_use(status="completed"),
            title="opencode",
            state=state,
        )

        assert len(events) == 1
        evt = events[0]
        assert isinstance(evt, ActionEvent)
        assert evt.phase == "completed"
        assert evt.action.kind == "question"


# ---------------------------------------------------------------------------
# question.py: callback data encoding/decoding
# ---------------------------------------------------------------------------


class TestQuestionCallbackData:
    def test_build_and_parse_roundtrip(self) -> None:
        data = build_question_callback_data("call_abc", 2)
        parsed = parse_question_callback_data(data)
        assert parsed == ("call_abc", 2)

    def test_parse_invalid_prefix(self) -> None:
        assert parse_question_callback_data("yee88:cancel") is None

    def test_parse_missing_index(self) -> None:
        assert (
            parse_question_callback_data(f"{QUESTION_CALLBACK_PREFIX}call_abc") is None
        )

    def test_parse_non_numeric_index(self) -> None:
        assert (
            parse_question_callback_data(f"{QUESTION_CALLBACK_PREFIX}call_abc:xyz")
            is None
        )

    def test_callback_data_starts_with_prefix(self) -> None:
        data = build_question_callback_data("id1", 0)
        assert data.startswith(QUESTION_CALLBACK_PREFIX)


# ---------------------------------------------------------------------------
# question.py: message formatting
# ---------------------------------------------------------------------------


class TestFormatQuestionMessage:
    def test_single_question_with_options(self) -> None:
        questions = [
            {
                "question": "Pick a color",
                "header": "Color",
                "options": [
                    {"label": "Red", "description": "Warm"},
                    {"label": "Blue", "description": "Cool"},
                ],
            }
        ]
        text = format_question_message(questions)
        assert "Color" in text
        assert "Pick a color" in text
        assert "Red" in text
        assert "Blue" in text
        assert "Warm" in text

    def test_question_without_header(self) -> None:
        questions = [{"question": "Yes or no?", "options": []}]
        text = format_question_message(questions)
        assert "Yes or no?" in text

    def test_empty_questions(self) -> None:
        assert format_question_message([]) == ""

    def test_plain_text_formatter(self) -> None:
        questions = [
            {
                "question": "Pick a color",
                "header": "Color",
                "options": [
                    {"label": "Red", "description": "Warm"},
                    {"label": "Blue"},
                ],
            }
        ]
        text = format_question_text_plain(questions)
        assert "Color" in text
        assert "Pick a color" in text
        assert "1. Red — Warm" in text
        assert "2. Blue" in text

    def test_disabled_notice_and_reply_include_guidance(self) -> None:
        questions = [{"question": "Which framework?", "options": [{"label": "React"}]}]
        notice = build_question_disabled_notice(questions)
        reply = build_question_disabled_reply(questions)
        assert "自动禁用" in notice
        assert "原始问题" in notice
        assert "Which framework?" in notice
        assert "Do not call the question tool again." in reply
        assert "Original question:" in reply


# ---------------------------------------------------------------------------
# question.py: keyboard building
# ---------------------------------------------------------------------------


class TestBuildQuestionKeyboard:
    def test_keyboard_has_option_buttons(self) -> None:
        questions = [
            {
                "question": "Pick",
                "options": [
                    {"label": "A"},
                    {"label": "B"},
                ],
                "custom": True,
            }
        ]
        kb = build_question_keyboard("call_1", questions)
        rows = kb["inline_keyboard"]
        # 2 option rows + 1 custom hint row
        assert len(rows) == 3
        assert rows[0][0]["text"] == "A"
        assert rows[1][0]["text"] == "B"
        assert "Type your own" in rows[2][0]["text"]

    def test_keyboard_no_custom_hint_when_disabled(self) -> None:
        questions = [
            {
                "question": "Pick",
                "options": [{"label": "A"}],
                "custom": False,
            }
        ]
        kb = build_question_keyboard("call_1", questions)
        rows = kb["inline_keyboard"]
        assert len(rows) == 1
        assert rows[0][0]["text"] == "A"

    def test_keyboard_empty_questions(self) -> None:
        kb = build_question_keyboard("call_1", [])
        assert kb["inline_keyboard"] == []

    def test_keyboard_callback_data_format(self) -> None:
        questions = [
            {
                "question": "Pick",
                "options": [{"label": "X"}],
            }
        ]
        kb = build_question_keyboard("call_1", questions)
        cb_data = kb["inline_keyboard"][0][0]["callback_data"]
        assert cb_data.startswith(QUESTION_CALLBACK_PREFIX)
        parsed = parse_question_callback_data(cb_data)
        assert parsed == ("call_1", 0)


# ---------------------------------------------------------------------------
# question.py: answer formatting
# ---------------------------------------------------------------------------


class TestFormatQuestionAnswer:
    def test_valid_option_index(self) -> None:
        questions = [
            {
                "question": "Pick",
                "options": [
                    {"label": "React"},
                    {"label": "Vue"},
                ],
            }
        ]
        assert format_question_answer(questions, 0) == "React"
        assert format_question_answer(questions, 1) == "Vue"

    def test_out_of_range_index(self) -> None:
        questions = [{"question": "Pick", "options": [{"label": "A"}]}]
        assert format_question_answer(questions, 5) == "Option 6"

    def test_empty_questions(self) -> None:
        assert format_question_answer([], 0) == ""


# ---------------------------------------------------------------------------
# question.py: handle_question_callback
# ---------------------------------------------------------------------------


class _FakeBot:
    """Minimal BotClient stub for handle_question_callback tests."""

    def __init__(self) -> None:
        self.answered: list[dict] = []

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        self.answered.append(
            {"id": callback_query_id, "text": text, "show_alert": show_alert}
        )
        return True


class _FakeCfg:
    """Minimal TelegramBridgeConfig stub."""

    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot


def _make_question_action_event(action_id: str = "call_q1") -> ActionEvent:
    return ActionEvent(
        engine="opencode",
        action=Action(
            id=action_id,
            kind="question",
            title="Framework",
            detail={
                "name": "question",
                "input": {},
                "callID": action_id,
                "questions": [
                    {
                        "question": "Which framework?",
                        "header": "Framework",
                        "options": [
                            {"label": "React", "description": "UI lib"},
                            {"label": "Vue", "description": "Progressive"},
                        ],
                        "multiple": False,
                        "custom": True,
                    }
                ],
            },
        ),
        phase="started",
    )


class TestHandleQuestionCallback:
    @pytest.mark.anyio
    async def test_returns_answer_and_resume_token(self) -> None:
        from yee88.telegram.commands.question import handle_question_callback

        bot = _FakeBot()
        cfg = _FakeCfg(bot)
        action_id = "call_q1"
        resume = ResumeToken(engine="opencode", value="ses_q1")
        pending = {action_id: _make_question_action_event(action_id)}
        tokens = {action_id: resume}

        query = TelegramCallbackQuery(
            transport="telegram",
            chat_id=123,
            message_id=10,
            callback_query_id="cb_1",
            data=build_question_callback_data(action_id, 0),
            sender_id=321,
        )

        result = await handle_question_callback(cfg, query, pending, tokens)  # type: ignore[arg-type]

        assert result is not None
        answer, returned_token = result
        assert answer == "React"
        assert returned_token is resume
        # Pending state should be cleaned up
        assert action_id not in pending
        assert action_id not in tokens

    @pytest.mark.anyio
    async def test_returns_answer_with_none_resume_token(self) -> None:
        from yee88.telegram.commands.question import handle_question_callback

        bot = _FakeBot()
        cfg = _FakeCfg(bot)
        action_id = "call_q2"
        pending = {action_id: _make_question_action_event(action_id)}
        tokens: dict[str, ResumeToken | None] = {action_id: None}

        query = TelegramCallbackQuery(
            transport="telegram",
            chat_id=123,
            message_id=10,
            callback_query_id="cb_2",
            data=build_question_callback_data(action_id, 1),
            sender_id=321,
        )

        result = await handle_question_callback(cfg, query, pending, tokens)  # type: ignore[arg-type]

        assert result is not None
        answer, returned_token = result
        assert answer == "Vue"
        assert returned_token is None

    @pytest.mark.anyio
    async def test_returns_none_for_expired_question(self) -> None:
        from yee88.telegram.commands.question import handle_question_callback

        bot = _FakeBot()
        cfg = _FakeCfg(bot)

        query = TelegramCallbackQuery(
            transport="telegram",
            chat_id=123,
            message_id=10,
            callback_query_id="cb_3",
            data=build_question_callback_data("nonexistent", 0),
            sender_id=321,
        )

        result = await handle_question_callback(cfg, query, {}, {})  # type: ignore[arg-type]

        assert result is None
        # Should have answered with "expired" message
        assert len(bot.answered) == 1
        assert "expired" in bot.answered[0]["text"].lower()

    @pytest.mark.anyio
    async def test_returns_none_for_non_question_callback(self) -> None:
        from yee88.telegram.commands.question import handle_question_callback

        bot = _FakeBot()
        cfg = _FakeCfg(bot)

        query = TelegramCallbackQuery(
            transport="telegram",
            chat_id=123,
            message_id=10,
            callback_query_id="cb_4",
            data="yee88:cancel",
            sender_id=321,
        )

        result = await handle_question_callback(cfg, query, {}, {})  # type: ignore[arg-type]
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_for_empty_data(self) -> None:
        from yee88.telegram.commands.question import handle_question_callback

        bot = _FakeBot()
        cfg = _FakeCfg(bot)

        query = TelegramCallbackQuery(
            transport="telegram",
            chat_id=123,
            message_id=10,
            callback_query_id="cb_5",
            data=None,
            sender_id=321,
        )

        result = await handle_question_callback(cfg, query, {}, {})  # type: ignore[arg-type]
        assert result is None


# ---------------------------------------------------------------------------
# Builtin directives: question tool prohibition is always injected
# ---------------------------------------------------------------------------


class TestBuiltinDirectives:
    @pytest.mark.anyio
    async def test_builtin_directives_always_present(self) -> None:
        from yee88.telegram.loop import _resolve_engine_run_options, _BUILTIN_DIRECTIVES

        result = await _resolve_engine_run_options(
            chat_id=123,
            thread_id=None,
            engine="opencode",
            chat_prefs=None,
            topic_store=None,
            system_prompt=None,
        )

        assert result is not None
        assert result.system is not None
        assert _BUILTIN_DIRECTIVES in result.system

    @pytest.mark.anyio
    async def test_builtin_directives_appended_to_user_prompt(self) -> None:
        from yee88.telegram.loop import _resolve_engine_run_options, _BUILTIN_DIRECTIVES

        user_prompt = "你是一个翻译助手"
        result = await _resolve_engine_run_options(
            chat_id=123,
            thread_id=None,
            engine="opencode",
            chat_prefs=None,
            topic_store=None,
            system_prompt=user_prompt,
        )

        assert result is not None
        assert result.system is not None
        # User prompt comes first
        assert result.system.startswith(user_prompt)
        # Builtin directives appended at the end
        assert result.system.endswith(_BUILTIN_DIRECTIVES)
        assert "question tool" in result.system

    @pytest.mark.anyio
    async def test_builtin_directives_not_overridden_by_user(self) -> None:
        from yee88.telegram.loop import _resolve_engine_run_options, _BUILTIN_DIRECTIVES

        # Even if user says "use question tool", builtin still appended
        user_prompt = "Always use the question tool to ask users"
        result = await _resolve_engine_run_options(
            chat_id=123,
            thread_id=None,
            engine="opencode",
            chat_prefs=None,
            topic_store=None,
            system_prompt=user_prompt,
        )

        assert result is not None
        assert result.system is not None
        assert _BUILTIN_DIRECTIVES in result.system
        # Builtin comes after user prompt (last word wins for LLMs)
        idx_user = result.system.index(user_prompt)
        idx_builtin = result.system.index(_BUILTIN_DIRECTIVES)
        assert idx_builtin > idx_user
