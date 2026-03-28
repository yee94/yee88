"""Handle question tool events from AI agents.

When an AI agent calls the ``question`` tool, the engine process blocks
waiting for user input.  In headless / bridge mode there is no interactive
terminal, so we surface the question in Telegram as an inline-keyboard
message and, once the user picks an option (or types a free-form reply),
cancel the blocked process and re-send the answer via a resume session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...logging import get_logger
from ...model import ActionEvent, ResumeToken
from ..types import TelegramCallbackQuery

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

logger = get_logger(__name__)

# Callback data prefix for question answers
QUESTION_CALLBACK_PREFIX = "yee88:question:"


def build_question_callback_data(
    action_id: str,
    option_index: int,
) -> str:
    """Encode an answer into callback_data (max 64 bytes for Telegram)."""
    return f"{QUESTION_CALLBACK_PREFIX}{action_id}:{option_index}"


def parse_question_callback_data(
    data: str,
) -> tuple[str, int] | None:
    """Decode callback_data → (action_id, option_index) or None."""
    if not data.startswith(QUESTION_CALLBACK_PREFIX):
        return None
    rest = data[len(QUESTION_CALLBACK_PREFIX) :]
    parts = rest.rsplit(":", 1)
    if len(parts) != 2:
        return None
    action_id, idx_str = parts
    try:
        return action_id, int(idx_str)
    except ValueError:
        return None


def format_question_message(questions: list[dict[str, Any]]) -> str:
    """Format question(s) into a human-readable Telegram message."""
    lines: list[str] = []
    for i, q in enumerate(questions):
        question_text = q.get("question", "")
        header = q.get("header", "")
        if header and header != question_text:
            lines.append(f"<b>{header}</b>")
        lines.append(question_text)
        options = q.get("options", [])
        if options:
            lines.append("")
            for j, opt in enumerate(options):
                label = opt.get("label", f"Option {j + 1}")
                desc = opt.get("description", "")
                if desc:
                    lines.append(f"  {j + 1}. <b>{label}</b> — {desc}")
                else:
                    lines.append(f"  {j + 1}. <b>{label}</b>")
        if i < len(questions) - 1:
            lines.append("")
    return "\n".join(lines)


def format_question_text_plain(questions: list[dict[str, Any]]) -> str:
    """Format question(s) into plain text for chat/user prompts."""
    lines: list[str] = []
    for i, q in enumerate(questions):
        question_text = str(q.get("question", "") or "").strip()
        header = str(q.get("header", "") or "").strip()
        if header and header != question_text:
            lines.append(header)
        if question_text:
            lines.append(question_text)
        options = q.get("options", [])
        if isinstance(options, list) and options:
            for j, opt in enumerate(options, start=1):
                label = str(opt.get("label", f"Option {j}") or f"Option {j}")
                desc = str(opt.get("description", "") or "").strip()
                if desc:
                    lines.append(f"{j}. {label} — {desc}")
                else:
                    lines.append(f"{j}. {label}")
        if i < len(questions) - 1:
            lines.append("")
    return "\n".join(lines).strip()


def build_question_disabled_notice(questions: list[dict[str, Any]]) -> str:
    question_text = format_question_text_plain(questions)
    lines = [
        "⚠️ AI 尝试调用 question tool。Telegram 侧已自动禁用这类交互，避免会话卡住。",
        "我已要求它改为下一条消息直接用文字向你提问。",
    ]
    if question_text:
        lines.extend(["", "原始问题：", question_text])
    return "\n".join(lines)


def build_question_disabled_reply(questions: list[dict[str, Any]]) -> str:
    question_text = format_question_text_plain(questions)
    lines = [
        "Question tool is unavailable in this Telegram chat.",
        "Do not call the question tool again.",
        "In your next assistant message, ask me the same thing directly in plain text and include short numbered options if needed.",
    ]
    if question_text:
        lines.extend(["", "Original question:", question_text])
    return "\n".join(lines)


def build_question_keyboard(
    action_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an inline keyboard for the first question's options.

    Each option becomes a button.  If ``custom`` is not explicitly False,
    a hint row is appended telling the user they can type a free-form reply.
    """
    if not questions:
        return {"inline_keyboard": []}

    first_q = questions[0]
    options = first_q.get("options", [])
    allows_custom = first_q.get("custom", True) is not False

    rows: list[list[dict[str, str]]] = []
    for j, opt in enumerate(options):
        label = opt.get("label", f"Option {j + 1}")
        cb_data = build_question_callback_data(action_id, j)
        # Telegram callback_data max is 64 bytes; truncate if needed
        if len(cb_data.encode("utf-8")) > 64:
            cb_data = cb_data[:64]
        rows.append([{"text": label, "callback_data": cb_data}])

    if allows_custom and options:
        rows.append(
            [
                {
                    "text": "💬 Type your own answer (reply to this message)",
                    "callback_data": "noop",
                }
            ]
        )

    return {"inline_keyboard": rows}


async def send_question_message(
    cfg: TelegramBridgeConfig,
    *,
    chat_id: int,
    action_event: ActionEvent,
    reply_to_message_id: int | None = None,
    thread_id: int | None = None,
) -> None:
    """Send a question message with inline keyboard to Telegram."""
    questions = action_event.action.detail.get("questions", [])
    if not questions:
        return

    text = format_question_message(questions)
    keyboard = build_question_keyboard(action_event.action.id, questions)

    await cfg.bot.send_message(
        chat_id=chat_id,
        text=f"❓ <b>Question from AI</b>\n\n{text}",
        reply_to_message_id=reply_to_message_id,
        message_thread_id=thread_id,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    logger.info(
        "question.sent",
        chat_id=chat_id,
        action_id=action_event.action.id,
        num_questions=len(questions),
    )


def format_question_answer(
    questions: list[dict[str, Any]],
    option_index: int,
) -> str:
    """Format the selected option as a user answer string for the AI."""
    if not questions:
        return ""
    first_q = questions[0]
    options = first_q.get("options", [])
    if 0 <= option_index < len(options):
        label = options[option_index].get("label", f"Option {option_index + 1}")
        return label
    return f"Option {option_index + 1}"


async def handle_question_callback(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    questions_pending: dict[str, ActionEvent],
    resume_tokens: dict[str, ResumeToken | None],
) -> tuple[str, ResumeToken | None] | None:
    """Handle a question callback query.

    Returns ``(answer, resume_token)`` if the callback was valid,
    or ``None`` if it was not a question callback.
    """
    if not query.data:
        return None

    parsed = parse_question_callback_data(query.data)
    if parsed is None:
        return None

    action_id, option_index = parsed
    action_event = questions_pending.get(action_id)
    if action_event is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="This question has expired.",
        )
        return None

    questions = action_event.action.detail.get("questions", [])
    answer = format_question_answer(questions, option_index)

    await cfg.bot.answer_callback_query(
        callback_query_id=query.callback_query_id,
        text=f"Selected: {answer}",
    )

    # Clean up – pop resume token but preserve it for the caller
    questions_pending.pop(action_id, None)
    resume_token = resume_tokens.pop(action_id, None)

    logger.info(
        "question.answered",
        action_id=action_id,
        answer=answer,
    )

    return answer, resume_token
