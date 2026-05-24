"""Tests for codebuddy stream-json schema decoding."""

from __future__ import annotations

import json
from pathlib import Path

from yee88.schemas import codebuddy as cb_schema


FIXTURE = Path(__file__).parent / "fixtures" / "codebuddy_stream_json_session.jsonl"


def _lines() -> list[bytes]:
    return [ln for ln in FIXTURE.read_bytes().splitlines() if ln.strip()]


def _by_type(events: list[object]) -> dict[str, list[object]]:
    out: dict[str, list[object]] = {}
    for ev in events:
        out.setdefault(type(ev).__name__, []).append(ev)
    return out


def test_decode_fixture_all_lines_succeed() -> None:
    """Every line in the fixture decodes without raising."""
    for line in _lines():
        cb_schema.decode_stream_json_line(line)


def test_decode_system_init() -> None:
    payload = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "uuid": "u1",
            "session_id": "sid",
            "cwd": "/tmp",
            "tools": ["Bash"],
            "mcp_servers": [],
            "model": "claude-opus-4.7-1m",
            "permissionMode": "bypassPermissions",
            "output_style": "default",
            "apiKeySource": "copilot.tencent.com",
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamSystemMessage)
    assert msg.subtype == "init"
    assert msg.session_id == "sid"
    assert msg.model == "claude-opus-4.7-1m"
    assert msg.permissionMode == "bypassPermissions"


def test_decode_system_status_is_system_message_with_subtype_status() -> None:
    """codebuddy emits `system.subtype=status` which must decode (and be ignored at translate time)."""
    payload = json.dumps(
        {
            "type": "system",
            "subtype": "status",
            "status": None,
            "uuid": "u2",
            "session_id": "sid",
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamSystemMessage)
    assert msg.subtype == "status"


def test_decode_file_history_snapshot() -> None:
    """codebuddy-exclusive `file-history-snapshot` rows must decode to a dedicated type."""
    payload = json.dumps(
        {
            "type": "file-history-snapshot",
            "id": "snap-1",
            "timestamp": 1779597209913,
            "isSnapshotUpdate": False,
            "snapshot": {"messageId": "m1", "trackedFileBackups": {}},
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.FileHistorySnapshot)
    assert msg.id == "snap-1"
    assert msg.timestamp == 1779597209913
    assert msg.isSnapshotUpdate is False


def test_decode_assistant_tool_use() -> None:
    payload = json.dumps(
        {
            "type": "assistant",
            "uuid": "u",
            "session_id": "sid",
            "parent_tool_use_id": None,
            "message": {
                "id": "m",
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ],
            },
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamAssistantMessage)
    assert len(msg.message.content) == 1
    block = msg.message.content[0]
    assert isinstance(block, cb_schema.StreamToolUseBlock)
    assert block.id == "tu_1"
    assert block.name == "Bash"


def test_decode_user_tool_result() -> None:
    payload = json.dumps(
        {
            "type": "user",
            "uuid": "u",
            "session_id": "sid",
            "parent_tool_use_id": "tu_1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": [{"type": "text", "text": "ok"}],
                        "is_error": False,
                    }
                ],
            },
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamUserMessage)
    content = msg.message.content
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, cb_schema.StreamToolResultBlock)
    assert block.tool_use_id == "tu_1"
    assert block.is_error is False


def test_decode_result_success() -> None:
    payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "uuid": "u",
            "session_id": "sid",
            "duration_ms": 100,
            "duration_api_ms": 80,
            "is_error": False,
            "num_turns": 2,
            "total_cost_usd": 0.0,
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "result": "hi",
            "permission_denials": [],
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamResultMessage)
    assert msg.is_error is False
    assert msg.result == "hi"


def test_decode_result_error() -> None:
    payload = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "uuid": "u",
            "session_id": "sid",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": True,
            "num_turns": 1,
            "result": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    ).encode()
    msg = cb_schema.decode_stream_json_line(payload)
    assert isinstance(msg, cb_schema.StreamResultMessage)
    assert msg.is_error is True
    assert msg.subtype == "error_during_execution"


def test_fixture_session_a_event_kinds() -> None:
    """The success session in the fixture contains the full event chain.

    Note: ``file-history-snapshot`` rows from codebuddy don't carry a
    ``session_id`` field, so we count snapshots across the whole file (both
    sessions emit one) rather than filtering by session_id.
    """
    events = [cb_schema.decode_stream_json_line(ln) for ln in _lines()]
    session_a_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    session_a = [e for e in events if getattr(e, "session_id", None) == session_a_id]
    by_kind = _by_type(session_a)
    assert "StreamSystemMessage" in by_kind
    assert any(s.subtype == "init" for s in by_kind["StreamSystemMessage"])
    assert any(s.subtype == "status" for s in by_kind["StreamSystemMessage"])
    assert "StreamAssistantMessage" in by_kind and len(by_kind["StreamAssistantMessage"]) == 2
    assert "StreamUserMessage" in by_kind and len(by_kind["StreamUserMessage"]) == 1
    assert "StreamResultMessage" in by_kind
    # Snapshots are emitted at the start of every session
    snapshots = [e for e in events if isinstance(e, cb_schema.FileHistorySnapshot)]
    assert len(snapshots) >= 1


def test_fixture_session_b_is_error() -> None:
    events = [cb_schema.decode_stream_json_line(ln) for ln in _lines()]
    session_b = [
        e for e in events if getattr(e, "session_id", None) == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    ]
    result = next(e for e in session_b if isinstance(e, cb_schema.StreamResultMessage))
    assert result.is_error is True
