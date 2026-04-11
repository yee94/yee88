import json
from pathlib import Path

import anyio
import pytest
import yee88.runners.opencode as opencode_runner

from yee88.config import ConfigError
from yee88.model import (
    ActionEvent,
    CompletedEvent,
    ResumeToken,
    StartedEvent,
    TextDeltaEvent,
    TextFinishedEvent,
)
from yee88.runners.opencode import (
    OpenCodeRunner,
    OpenCodeStreamState,
    ENGINE,
    translate_opencode_event,
    _select_debug_primary_agent,
    build_runner,
)
from yee88.schemas import opencode as opencode_schema


def _load_fixture(name: str) -> list[opencode_schema.OpenCodeEvent]:
    path = Path(__file__).parent / "fixtures" / name
    events: list[opencode_schema.OpenCodeEvent] = []
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            events.append(opencode_schema.decode_event(line))
        except Exception as exc:
            raise AssertionError(
                f"{name} contained unparseable line: {line!r}"
            ) from exc
    return events


def _decode_event(payload: dict) -> opencode_schema.OpenCodeEvent:
    return opencode_schema.decode_event(json.dumps(payload).encode("utf-8"))


def test_opencode_resume_format_and_extract() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    token = ResumeToken(engine=ENGINE, value="ses_abc123")

    assert runner.extract_resume("`opencode --session ses_abc123`") == token
    assert runner.extract_resume("opencode run -s ses_other") == ResumeToken(
        engine=ENGINE, value="ses_other"
    )
    assert runner.extract_resume("opencode -s ses_other") == ResumeToken(
        engine=ENGINE, value="ses_other"
    )
    assert runner.extract_resume("`claude --resume sid`") is None
    assert runner.extract_resume("`codex resume sid`") is None


def test_translate_success_fixture() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_success.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    assert isinstance(events[0], StartedEvent)
    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.resume.value == "ses_494719016ffe85dkDMj0FPRbHK"
    assert started.resume.engine == ENGINE

    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert len(action_events) == 1

    completed_actions = [evt for evt in action_events if evt.phase == "completed"]
    assert len(completed_actions) == 1
    assert completed_actions[0].action.kind == "command"
    assert completed_actions[0].ok is True

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert events[-1] == completed
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "```\nhello\n```"


def test_translate_missing_reason_success() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_success_no_reason.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    runner = OpenCodeRunner(opencode_cmd="opencode")
    fallback = runner.stream_end_events(
        resume=None,
        found_session=started.resume,
        state=state,
    )

    completed = next(evt for evt in fallback if isinstance(evt, CompletedEvent))
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "All done."


def test_translate_accumulates_text() -> None:
    state = OpenCodeStreamState()

    events = translate_opencode_event(
        _decode_event({"type": "step_start", "sessionID": "ses_test123", "part": {}}),
        title="opencode",
        state=state,
    )
    assert len(events) == 1
    assert isinstance(events[0], StartedEvent)

    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Hello "},
            }
        ),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "World"},
            }
        ),
        title="opencode",
        state=state,
    )

    assert state.last_text == "Hello World"

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {"reason": "stop", "tokens": {"input": 100, "output": 10}},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, CompletedEvent)
    assert completed.answer == "Hello World"
    assert completed.ok is True


def test_translate_tool_use_completed() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "ls -la"},
                        "output": "file1.txt\nfile2.txt",
                        "title": "List files",
                        "metadata": {"exit": 0},
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.phase == "completed"
    assert action_event.action.kind == "command"
    assert action_event.action.title == "List files"
    assert action_event.ok is True


def test_translate_tool_use_with_error() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "exit 1"},
                        "output": "error",
                        "title": "Run failing command",
                        "metadata": {"exit": 1},
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.phase == "completed"
    assert action_event.ok is False


def test_translate_tool_use_read_title_wraps_path() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True
    path = Path.cwd() / "src" / "yee88" / "runners" / "opencode.py"

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": str(path)},
                        "output": "file contents",
                        "title": "src/yee88/runners/opencode.py",
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.action.kind == "tool"
    assert action_event.action.title == "`src/yee88/runners/opencode.py`"


def test_translate_error_fixture() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_error.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))

    assert completed.ok is False
    assert completed.error == "Rate limit exceeded"
    assert completed.resume == started.resume


def test_step_finish_tool_calls_does_not_complete() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {
                    "reason": "tool-calls",
                    "tokens": {"input": 100, "output": 10},
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 0


def test_build_args_new_session() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode", model="claude-sonnet")
    runner.resolve_agent = lambda: None  # type: ignore[method-assign]
    args = runner.build_args("hello world", None, state=OpenCodeStreamState())

    assert args == [
        "run",
        "--format",
        "json",
        "--model",
        "claude-sonnet",
        "--",
        "hello world",
    ]


def test_select_debug_primary_agent_prefers_build() -> None:
    payload = {
        "default_agent": "primary-a",
        "agent": {
            "build": {"mode": "primary"},
            "primary-a": {"mode": "primary"},
        },
    }

    assert _select_debug_primary_agent(payload) == "build"


def test_select_debug_primary_agent_uses_default_agent() -> None:
    payload = {
        "default_agent": "\u200bSisyphus - Ultraworker",
        "agent": {
            "build": {"mode": "subagent", "hidden": True},
            "\u200bSisyphus - Ultraworker": {"mode": "primary"},
            "fallback": {"mode": "primary"},
        },
    }

    assert _select_debug_primary_agent(payload) == "\u200bSisyphus - Ultraworker"


def test_build_args_new_session_with_configured_agent() -> None:
    runner = OpenCodeRunner(
        opencode_cmd="opencode",
        model="claude-sonnet",
        agent="primary-agent",
    )
    args = runner.build_args("hello world", None, state=OpenCodeStreamState())

    assert args == [
        "run",
        "--format",
        "json",
        "--agent",
        "primary-agent",
        "--model",
        "claude-sonnet",
        "--",
        "hello world",
    ]


def test_build_args_new_session_with_discovered_agent(monkeypatch) -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode", model="claude-sonnet")
    monkeypatch.setattr(
        opencode_runner,
        "_resolve_opencode_primary_agent",
        lambda *_args: "\u200bSisyphus - Ultraworker",
    )

    args = runner.build_args("hello world", None, state=OpenCodeStreamState())

    assert args[:5] == [
        "run",
        "--format",
        "json",
        "--agent",
        "\u200bSisyphus - Ultraworker",
    ]


def test_build_args_with_resume() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode", agent="primary-agent")
    token = ResumeToken(engine=ENGINE, value="ses_abc123")
    args = runner.build_args("continue", token, state=OpenCodeStreamState())

    assert args == [
        "run",
        "--format",
        "json",
        "--session",
        "ses_abc123",
        "--",
        "continue",
    ]


def test_build_runner_reads_agent_alias() -> None:
    runner = build_runner({"model": "foo", "main_agent": "primary-agent"}, Path("cfg"))

    assert isinstance(runner, OpenCodeRunner)
    assert runner.agent == "primary-agent"


def test_build_runner_rejects_non_string_agent() -> None:
    with pytest.raises(ConfigError):
        build_runner({"agent": ["bad"]}, Path("cfg"))


def test_stdin_payload_returns_none() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    payload = runner.stdin_payload("prompt", None, state=OpenCodeStreamState())
    assert payload is None


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await gate.wait()
            yield CompletedEvent(
                engine=ENGINE,
                resume=ResumeToken(engine=ENGINE, value="ses_test"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="ses_test")
    async with anyio.create_task_group() as tg:
        tg.start_soon(drain, "a", token)
        tg.start_soon(drain, "b", token)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1


# --- TextFinishedEvent tests ---


def test_step_finish_tool_calls_emits_text_finished() -> None:
    """step_finish(tool-calls) with accumulated text emits TextFinishedEvent and resets last_text."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True
    state.last_text = "I'll analyze the code"

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {
                    "reason": "tool-calls",
                    "tokens": {"input": 100, "output": 10},
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    evt = events[0]
    assert isinstance(evt, TextFinishedEvent)
    assert evt.type == "text_finished"
    assert evt.engine == ENGINE
    assert evt.text == "I'll analyze the code"
    # last_text must be reset so next step starts fresh
    assert state.last_text is None


def test_step_finish_tool_calls_without_text_no_event() -> None:
    """step_finish(tool-calls) without accumulated text emits nothing."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True
    # last_text is None — no text accumulated

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {
                    "reason": "tool-calls",
                    "tokens": {"input": 100, "output": 10},
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 0


def test_multi_step_agent_text_independent() -> None:
    """Multi-step agent: each step's text is independent, final answer only contains last step."""
    state = OpenCodeStreamState()

    # Step 1: step_start → text → step_finish(tool-calls)
    translate_opencode_event(
        _decode_event({"type": "step_start", "sessionID": "ses_multi", "part": {}}),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_multi",
                "part": {"type": "text", "text": "Step 1 thinking"},
            }
        ),
        title="opencode",
        state=state,
    )
    step1_events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_multi",
                "part": {"reason": "tool-calls"},
            }
        ),
        title="opencode",
        state=state,
    )
    assert len(step1_events) == 1
    assert isinstance(step1_events[0], TextFinishedEvent)
    assert step1_events[0].text == "Step 1 thinking"
    assert state.last_text is None

    # Step 2: step_start → text → step_finish(tool-calls)
    translate_opencode_event(
        _decode_event({"type": "step_start", "sessionID": "ses_multi", "part": {}}),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_multi",
                "part": {"type": "text", "text": "Step 2 analysis"},
            }
        ),
        title="opencode",
        state=state,
    )
    step2_events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_multi",
                "part": {"reason": "tool-calls"},
            }
        ),
        title="opencode",
        state=state,
    )
    assert len(step2_events) == 1
    assert isinstance(step2_events[0], TextFinishedEvent)
    assert step2_events[0].text == "Step 2 analysis"
    assert state.last_text is None

    # Step 3: step_start → text → step_finish(stop) → CompletedEvent
    translate_opencode_event(
        _decode_event({"type": "step_start", "sessionID": "ses_multi", "part": {}}),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_multi",
                "part": {"type": "text", "text": "Final answer"},
            }
        ),
        title="opencode",
        state=state,
    )
    step3_events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_multi",
                "part": {"reason": "stop"},
            }
        ),
        title="opencode",
        state=state,
    )
    assert len(step3_events) == 1
    completed = step3_events[0]
    assert isinstance(completed, CompletedEvent)
    # Final answer must only contain the last step's text, not accumulated
    assert completed.answer == "Final answer"
    assert completed.ok is True


def test_text_accumulated_within_step_is_correct() -> None:
    """Text chunks within a single step are correctly accumulated."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    # Simulate multiple text chunks within one step
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Hello "},
            }
        ),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "World"},
            }
        ),
        title="opencode",
        state=state,
    )

    # step_finish(tool-calls) should emit the accumulated text
    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {"reason": "tool-calls"},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    assert isinstance(events[0], TextFinishedEvent)
    assert events[0].text == "Hello World"
    assert state.last_text is None

    # Next step starts fresh
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "New step text"},
            }
        ),
        title="opencode",
        state=state,
    )
    assert state.last_text == "New step text"


# --- TextDeltaEvent tests ---


def test_text_event_emits_text_delta() -> None:
    """Each text event should emit a TextDeltaEvent with accumulated snapshot."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Hello "},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    evt = events[0]
    assert isinstance(evt, TextDeltaEvent)
    assert evt.snapshot == "Hello "

    # Second chunk accumulates
    events2 = translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "World"},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events2) == 1
    evt2 = events2[0]
    assert isinstance(evt2, TextDeltaEvent)
    assert evt2.snapshot == "Hello World"


def test_text_event_empty_no_delta() -> None:
    """Empty text events should not emit TextDeltaEvent."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": ""},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 0


def test_text_delta_resets_after_text_finished() -> None:
    """After TextFinishedEvent, next text delta starts fresh."""
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    # Accumulate text
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Step 1"},
            }
        ),
        title="opencode",
        state=state,
    )

    # step_finish(tool-calls) emits TextFinishedEvent and resets
    translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {"reason": "tool-calls"},
            }
        ),
        title="opencode",
        state=state,
    )
    assert state.last_text is None

    # Next text starts fresh
    events = translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Step 2"},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].snapshot == "Step 2"
