"""Tests for the CodeBuddy runner: resume regex, translate, build_args, env, fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import anyio
import msgspec
import pytest

import yee88.runners.codebuddy as cb_runner
from yee88.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from yee88.runners.codebuddy import (
    CodeBuddyRunner,
    CodeBuddyStreamState,
    ENGINE,
    translate_codebuddy_event,
)
from yee88.schemas import codebuddy as cb_schema


FIXTURE = Path(__file__).parent / "fixtures" / "codebuddy_stream_json_session.jsonl"


def _load_fixture(session_id: str | None = None) -> list[cb_schema.StreamJsonMessage]:
    events = [
        cb_schema.decode_stream_json_line(line)
        for line in FIXTURE.read_bytes().splitlines()
        if line.strip()
    ]
    if session_id is None:
        return events
    # snapshots have no session_id; keep them grouped with the closest session
    out: list[cb_schema.StreamJsonMessage] = []
    current: str | None = None
    for ev in events:
        sid = getattr(ev, "session_id", None)
        if sid:
            current = sid
        if (sid == session_id) or (sid is None and current == session_id):
            out.append(ev)
    return out


def _decode_event(payload: dict) -> cb_schema.StreamJsonMessage:
    data_payload = dict(payload)
    data_payload.setdefault("uuid", "uuid")
    data_payload.setdefault("session_id", "session")
    match data_payload.get("type"):
        case "assistant":
            message = dict(data_payload.get("message", {}))
            message.setdefault("role", "assistant")
            message.setdefault("content", [])
            message.setdefault("model", "claude-opus-4-7")
            data_payload["message"] = message
        case "user":
            message = dict(data_payload.get("message", {}))
            message.setdefault("role", "user")
            message.setdefault("content", [])
            data_payload["message"] = message
    data = json.dumps(data_payload).encode("utf-8")
    return cb_schema.decode_stream_json_line(data)


# --------------------------------------------------------------------------- #
# resume extraction
# --------------------------------------------------------------------------- #


def test_codebuddy_resume_format_and_extract() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    token = ResumeToken(engine=ENGINE, value="sid")

    assert runner.extract_resume("`codebuddy --resume sid`") == token
    assert runner.extract_resume("codebuddy -r other") == ResumeToken(
        engine=ENGINE, value="other"
    )
    # other engines' resume lines must not be picked up
    assert runner.extract_resume("`claude --resume sid`") is None
    assert runner.extract_resume("`codex resume sid`") is None


# --------------------------------------------------------------------------- #
# build_args
# --------------------------------------------------------------------------- #


def test_build_args_basic_includes_print_stream_json_verbose() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    args = runner._build_args("hello", None)
    assert args[:4] == ["-p", "--output-format", "stream-json", "--verbose"]
    assert args[-2:] == ["--", "hello"]
    assert "--resume" not in args


def test_build_args_with_resume_and_model() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy", model="claude-opus-4.7-1m")
    token = ResumeToken(engine=ENGINE, value="sid-xyz")
    args = runner._build_args("hi", token)
    assert "--resume" in args
    assert args[args.index("--resume") + 1] == "sid-xyz"
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-opus-4.7-1m"


def test_build_args_with_allowed_tools_list() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy", allowed_tools=["Bash", "Read"])
    args = runner._build_args("hi", None)
    assert "--allowedTools" in args
    assert args[args.index("--allowedTools") + 1] == "Bash,Read"


# --------------------------------------------------------------------------- #
# env: must strip ANTHROPIC_API_KEY so codebuddy uses its own (Tencent) backend
# --------------------------------------------------------------------------- #


def test_env_strips_anthropic_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    env = runner.env(state=CodeBuddyStreamState())
    assert env is not None
    assert "ANTHROPIC_API_KEY" not in env


# --------------------------------------------------------------------------- #
# build_runner factory
# --------------------------------------------------------------------------- #


def test_build_runner_uses_shutil_which(monkeypatch) -> None:
    expected = "/opt/codebuddy/bin/codebuddy"
    called: dict[str, str] = {}

    def fake_which(name: str) -> str | None:
        called["name"] = name
        return expected

    monkeypatch.setattr(cb_runner.shutil, "which", fake_which)
    runner = cast(CodeBuddyRunner, cb_runner.build_runner({}, Path("yee88.toml")))

    assert called["name"] == "codebuddy"
    assert runner.codebuddy_cmd == expected


def test_build_runner_default_model_when_unset(monkeypatch) -> None:
    """When [codebuddy].model is not in yee88.toml, fall back to DEFAULT_MODEL."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(CodeBuddyRunner, cb_runner.build_runner({}, Path("yee88.toml")))
    assert runner.model == cb_runner.DEFAULT_MODEL
    assert cb_runner.DEFAULT_MODEL == "claude-sonnet-4.6"
    assert runner.session_title == cb_runner.DEFAULT_MODEL


def test_build_runner_user_model_overrides_default(monkeypatch) -> None:
    """An explicit `model` in EngineConfig must win over DEFAULT_MODEL."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(
        CodeBuddyRunner,
        cb_runner.build_runner({"model": "claude-opus-4.7-1m"}, Path("yee88.toml")),
    )
    assert runner.model == "claude-opus-4.7-1m"
    assert runner.session_title == "claude-opus-4.7-1m"


def test_build_runner_explicit_none_keeps_default(monkeypatch) -> None:
    """Explicit ``model = None`` (rare) should still get the fallback default."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(
        CodeBuddyRunner,
        cb_runner.build_runner({"model": None}, Path("yee88.toml")),
    )
    assert runner.model == cb_runner.DEFAULT_MODEL


def test_build_runner_default_skips_permissions(monkeypatch) -> None:
    """No config → permissions are skipped (non-interactive bot use case)."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(CodeBuddyRunner, cb_runner.build_runner({}, Path("yee88.toml")))
    assert runner.dangerously_skip_permissions is True
    args = runner._build_args("hi", None)
    assert "--dangerously-skip-permissions" in args


def test_build_runner_explicit_false_disables_skip(monkeypatch) -> None:
    """Users can still opt out by setting the config to false."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(
        CodeBuddyRunner,
        cb_runner.build_runner(
            {"dangerously_skip_permissions": False}, Path("yee88.toml")
        ),
    )
    assert runner.dangerously_skip_permissions is False
    args = runner._build_args("hi", None)
    assert "--dangerously-skip-permissions" not in args


def test_default_args_send_default_model(monkeypatch) -> None:
    """The default-built runner must actually emit ``--model claude-sonnet-4.6`` to CLI."""
    monkeypatch.setattr(cb_runner.shutil, "which", lambda _name: "codebuddy")
    runner = cast(CodeBuddyRunner, cb_runner.build_runner({}, Path("yee88.toml")))
    args = runner._build_args("hi", None)
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-4.6"


def test_backend_is_registered() -> None:
    assert cb_runner.BACKEND.id == "codebuddy"
    assert cb_runner.BACKEND.install_cmd is not None
    assert "codebuddy" in cb_runner.BACKEND.install_cmd


def test_backend_discoverable_via_entrypoint() -> None:
    """codebuddy must be loadable through the engine entrypoint registry."""
    from yee88.engines import get_backend, list_backend_ids

    assert "codebuddy" in list_backend_ids()
    backend = get_backend("codebuddy")
    assert backend.id == "codebuddy"
    assert backend.build_runner is cb_runner.build_runner


# --------------------------------------------------------------------------- #
# translate
# --------------------------------------------------------------------------- #


def test_translate_success_fixture() -> None:
    state = CodeBuddyStreamState()
    events: list = []
    for event in _load_fixture(session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
        events.extend(
            translate_codebuddy_event(
                event,
                title="codebuddy",
                state=state,
                factory=state.factory,
            )
        )

    # Started event is first, with session id as resume token
    started_evt = next(e for e in events if isinstance(e, StartedEvent))
    assert started_evt.resume.engine == ENGINE
    assert started_evt.resume.value == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    # One tool_use action started + one matching tool_result completed
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    started_actions = {e.action.id: e for e in action_events if e.phase == "started"}
    completed_actions = {
        e.action.id: e for e in action_events if e.phase == "completed"
    }
    assert "toolu_bdrk_001" in started_actions
    assert started_actions["toolu_bdrk_001"].action.kind == "command"
    assert "toolu_bdrk_001" in completed_actions
    assert completed_actions["toolu_bdrk_001"].ok is True

    # Final completed event carries the assistant's text answer
    completed = next(e for e in events if isinstance(e, CompletedEvent))
    assert events[-1] == completed
    assert completed.ok is True
    assert completed.resume == started_evt.resume
    assert "Three jsonl files" in completed.answer


def test_translate_error_fixture() -> None:
    state = CodeBuddyStreamState()
    events: list = []
    for event in _load_fixture(session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"):
        events.extend(
            translate_codebuddy_event(
                event,
                title="codebuddy",
                state=state,
                factory=state.factory,
            )
        )

    started = next(e for e in events if isinstance(e, StartedEvent))
    completed = next(e for e in events if isinstance(e, CompletedEvent))
    assert completed.ok is False
    assert completed.error is not None
    assert "codebuddy" in completed.error.lower() or "error" in completed.error.lower()
    assert completed.resume == started.resume


def test_translate_ignores_system_status() -> None:
    """system.subtype=status rows must produce zero events (not raise)."""
    state = CodeBuddyStreamState()
    msg = _decode_event(
        {
            "type": "system",
            "subtype": "status",
            "status": None,
            "session_id": "sid",
        }
    )
    out = translate_codebuddy_event(
        msg, title="codebuddy", state=state, factory=state.factory
    )
    assert out == []


def test_translate_ignores_file_history_snapshot() -> None:
    """file-history-snapshot rows must produce zero events (not raise)."""
    state = CodeBuddyStreamState()
    msg = cb_schema.decode_stream_json_line(
        json.dumps(
            {
                "type": "file-history-snapshot",
                "id": "snap-1",
                "timestamp": 1779597209913,
                "isSnapshotUpdate": False,
                "snapshot": {"messageId": "m1", "trackedFileBackups": {}},
            }
        ).encode()
    )
    out = translate_codebuddy_event(
        msg, title="codebuddy", state=state, factory=state.factory
    )
    assert out == []


def test_tool_results_pop_pending_actions() -> None:
    state = CodeBuddyStreamState()

    tool_use_event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "echo hi"},
                }
            ],
        },
    }
    tool_result_event = {
        "type": "user",
        "message": {
            "id": "msg_2",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "ok",
                    "is_error": False,
                }
            ],
        },
    }

    translate_codebuddy_event(
        _decode_event(tool_use_event),
        title="codebuddy",
        state=state,
        factory=state.factory,
    )
    assert "toolu_1" in state.pending_actions

    translate_codebuddy_event(
        _decode_event(tool_result_event),
        title="codebuddy",
        state=state,
        factory=state.factory,
    )
    assert not state.pending_actions


def test_translate_thinking_block() -> None:
    state = CodeBuddyStreamState()
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Consider the options.",
                    "signature": "sig",
                }
            ],
        },
    }

    events = translate_codebuddy_event(
        _decode_event(event),
        title="codebuddy",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    assert isinstance(events[0], ActionEvent)
    assert events[0].phase == "completed"
    assert events[0].action.kind == "note"
    assert events[0].action.title == "Consider the options."


def test_translate_ask_user_question_emits_question_kind() -> None:
    """When codebuddy invokes its AskUserQuestion tool, the translator must
    label the action with kind="question" so the Telegram bridge can route
    it through ``_on_question`` (cancel job + send disabled-notice).

    The CLI sometimes emits the tool name as 'AskUserQuestion' or simply
    'question' depending on context — both must hit the same kind.
    """
    state = CodeBuddyStreamState()
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_q",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_q1",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [
                            {
                                "header": "Engine choice",
                                "question": "Which engine should I use?",
                                "options": [
                                    {"label": "claude"},
                                    {"label": "codebuddy"},
                                ],
                            }
                        ]
                    },
                }
            ],
        },
    }

    events = translate_codebuddy_event(
        _decode_event(event),
        title="codebuddy",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.phase == "started"
    assert action_event.action.kind == "question"
    # The detail dict carries the raw input so the bridge can render the
    # questions/options to Telegram (or, today, the disabled-notice).
    questions = action_event.action.detail["input"]["questions"]
    assert questions[0]["header"] == "Engine choice"
    assert len(questions[0]["options"]) == 2


# --------------------------------------------------------------------------- #
# concurrent run serialization (per session)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
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
                resume=ResumeToken(engine=ENGINE, value="sid"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="sid")
    async with anyio.create_task_group() as tg:
        tg.start_soon(drain, "a", token)
        tg.start_soon(drain, "b", token)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1


# --------------------------------------------------------------------------- #
# error-path coverage: process_error_events / stream_end_events / decode errors
# --------------------------------------------------------------------------- #


def test_process_error_events_emits_note_and_completed_error() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    events = runner.process_error_events(
        rc=2,
        resume=None,
        found_session=ResumeToken(engine=ENGINE, value="sid"),
        state=state,
        stderr="boom\nstack trace\n",
    )
    assert len(events) == 2
    note_evt, completed_evt = events
    assert isinstance(note_evt, ActionEvent)
    assert note_evt.action.kind == "warning"
    assert "rc=2" in note_evt.message
    assert "boom" in note_evt.message
    assert isinstance(completed_evt, CompletedEvent)
    assert completed_evt.ok is False
    assert "rc=2" in (completed_evt.error or "")
    assert completed_evt.resume == ResumeToken(engine=ENGINE, value="sid")


def test_process_error_events_falls_back_to_resume_when_no_session() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    original = ResumeToken(engine=ENGINE, value="orig")
    events = runner.process_error_events(
        rc=1,
        resume=original,
        found_session=None,
        state=state,
        stderr="",
    )
    completed = events[-1]
    assert isinstance(completed, CompletedEvent)
    assert completed.resume == original


def test_stream_end_events_without_session_returns_error() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    events = runner.stream_end_events(
        resume=None,
        found_session=None,
        state=state,
        stderr="weird tail",
    )
    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, CompletedEvent)
    assert completed.ok is False
    assert "no session_id" in (completed.error or "")
    assert "weird tail" in (completed.error or "")


def test_stream_end_events_with_session_carries_last_text() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    state.last_assistant_text = "draft answer"
    session = ResumeToken(engine=ENGINE, value="sid-end")
    events = runner.stream_end_events(
        resume=None,
        found_session=session,
        state=state,
    )
    completed = events[0]
    assert isinstance(completed, CompletedEvent)
    assert completed.ok is False
    assert completed.answer == "draft answer"
    assert completed.resume == session


def test_decode_error_events_swallows_msgspec_decode_error() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    err = msgspec.DecodeError("unknown tag")
    events = runner.decode_error_events(
        raw="{bad}", line="{bad}", error=err, state=state
    )
    assert events == []


def test_invalid_json_events_returns_empty() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    assert runner.invalid_json_events(raw="not json", line="not json", state=state) == []


def test_stdin_payload_is_none() -> None:
    """codebuddy receives the prompt via argv, not stdin."""
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    state = CodeBuddyStreamState()
    assert runner.stdin_payload("prompt", None, state=state) is None


def test_new_state_returns_fresh_state() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="codebuddy")
    s1 = runner.new_state("p", None)
    s2 = runner.new_state("p", None)
    assert s1 is not s2
    assert isinstance(s1, CodeBuddyStreamState)


def test_command_returns_configured_cmd() -> None:
    runner = CodeBuddyRunner(codebuddy_cmd="/opt/cb/codebuddy")
    assert runner.command() == "/opt/cb/codebuddy"


# --------------------------------------------------------------------------- #
# end-to-end with a fake codebuddy binary
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_run_strips_anthropic_api_key_in_subprocess(tmp_path, monkeypatch) -> None:
    """A fake codebuddy script writes the env var status into the result.

    Verifies that the runner's ``env()`` strip is actually applied to the
    spawned subprocess, not just returned from the method in isolation.
    """
    codebuddy_path = tmp_path / "codebuddy"
    codebuddy_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "\n"
        "session_id = 'session_01'\n"
        "status = 'set' if os.environ.get('ANTHROPIC_API_KEY') else 'unset'\n"
        "init = {\n"
        "    'type': 'system',\n"
        "    'subtype': 'init',\n"
        "    'uuid': 'uuid',\n"
        "    'session_id': session_id,\n"
        "    'apiKeySource': 'copilot.tencent.com',\n"
        "    'cwd': '.',\n"
        "    'tools': [],\n"
        "    'mcp_servers': [],\n"
        "    'model': 'claude-opus-4.7-1m',\n"
        "    'permissionMode': 'bypassPermissions',\n"
        "    'output_style': 'default',\n"
        "}\n"
        "print(json.dumps(init), flush=True)\n"
        "status_line = {\n"
        "    'type': 'system',\n"
        "    'subtype': 'status',\n"
        "    'status': None,\n"
        "    'uuid': 'uuid2',\n"
        "    'session_id': session_id,\n"
        "}\n"
        "print(json.dumps(status_line), flush=True)\n"
        "snapshot = {\n"
        "    'type': 'file-history-snapshot',\n"
        "    'id': 'snap-1',\n"
        "    'timestamp': 1,\n"
        "    'isSnapshotUpdate': False,\n"
        "    'snapshot': {'messageId': 'm', 'trackedFileBackups': {}},\n"
        "}\n"
        "print(json.dumps(snapshot), flush=True)\n"
        "result = {\n"
        "    'type': 'result',\n"
        "    'subtype': 'success',\n"
        "    'uuid': 'uuid3',\n"
        "    'session_id': session_id,\n"
        "    'duration_ms': 0,\n"
        "    'duration_api_ms': 0,\n"
        "    'is_error': False,\n"
        "    'num_turns': 1,\n"
        "    'result': f'api={status}',\n"
        "    'total_cost_usd': 0.0,\n"
        "    'usage': {'input_tokens': 0, 'output_tokens': 0},\n"
        "    'permission_denials': [],\n"
        "}\n"
        "print(json.dumps(result), flush=True)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    codebuddy_path.chmod(0o755)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    runner = CodeBuddyRunner(codebuddy_cmd=str(codebuddy_path))
    answer: str | None = None
    started_seen = False
    snapshot_did_not_crash = True
    async for event in runner.run("hello", None):
        if isinstance(event, StartedEvent):
            started_seen = True
        if isinstance(event, CompletedEvent):
            answer = event.answer
    assert started_seen
    assert snapshot_did_not_crash  # snapshot row was tolerated upstream
    assert answer == "api=unset"


# --------------------------------------------------------------------------- #
# live smoke against real codebuddy CLI (opt-in: `pytest -m live`)
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.anyio
async def test_live_codebuddy_cli_smoke(tmp_path) -> None:
    """Real end-to-end: spawn the actual codebuddy CLI, expect a session + answer.

    Skipped by default. Run with: ``uv run pytest -m live``.
    """
    import shutil as _shutil

    cmd = _shutil.which("codebuddy")
    if cmd is None:
        pytest.skip("codebuddy binary not found on PATH")

    runner = CodeBuddyRunner(
        codebuddy_cmd=cmd,
        dangerously_skip_permissions=True,
    )

    # Use tmp_path as cwd indirectly by overriding os env CWD? The runner
    # inherits cwd from the current process; tmp_path is just to keep state
    # isolated. CodeBuddy itself only cares about the prompt here.

    started_token: ResumeToken | None = None
    completed: CompletedEvent | None = None
    async for event in runner.run(
        "Reply with exactly: codebuddy-yee88-live", None
    ):
        if isinstance(event, StartedEvent):
            started_token = event.resume
        elif isinstance(event, CompletedEvent):
            completed = event

    assert started_token is not None, "no StartedEvent observed"
    assert completed is not None, "no CompletedEvent observed"
    assert completed.ok is True, f"run failed: {completed.error!r}"
    assert "codebuddy-yee88-live" in completed.answer
    assert completed.resume == started_token


@pytest.mark.live
@pytest.mark.anyio
async def test_live_codebuddy_resume_reuses_session() -> None:
    """Second turn with --resume keeps the same session_id."""
    import shutil as _shutil

    cmd = _shutil.which("codebuddy")
    if cmd is None:
        pytest.skip("codebuddy binary not found on PATH")

    runner = CodeBuddyRunner(
        codebuddy_cmd=cmd,
        dangerously_skip_permissions=True,
    )

    first_resume: ResumeToken | None = None
    async for event in runner.run("Reply with exactly: turn-1", None):
        if isinstance(event, CompletedEvent):
            first_resume = event.resume

    assert first_resume is not None

    second_resume: ResumeToken | None = None
    second_answer = ""
    async for event in runner.run("Reply with exactly: turn-2", first_resume):
        if isinstance(event, CompletedEvent):
            second_resume = event.resume
            second_answer = event.answer

    assert second_resume is not None
    assert second_resume.value == first_resume.value, "session_id must be reused on resume"
    assert "turn-2" in second_answer


@pytest.mark.live
@pytest.mark.anyio
async def test_live_default_runner_uses_default_model() -> None:
    """A runner built via ``build_runner({})`` actually invokes codebuddy with
    the configured DEFAULT_MODEL and reaches a successful Completed event.
    """
    import shutil as _shutil

    if _shutil.which("codebuddy") is None:
        pytest.skip("codebuddy binary not on PATH")

    runner = cast(CodeBuddyRunner, cb_runner.build_runner({}, Path("yee88.toml")))
    runner.dangerously_skip_permissions = True
    assert runner.model == cb_runner.DEFAULT_MODEL

    completed: CompletedEvent | None = None
    async for event in runner.run("Reply with exactly: cb-default-ok", None):
        if isinstance(event, CompletedEvent):
            completed = event

    assert completed is not None
    assert completed.ok is True, f"run failed: {completed.error!r}"
    assert "cb-default-ok" in completed.answer
