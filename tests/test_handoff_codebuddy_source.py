"""Tests for CodeBuddy handoff source: list/messages/model parsing."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from yee88.cli.handoff_sources import codebuddy as cb_src


def _write_session_jsonl(
    project_dir: Path,
    session_id: str,
    *,
    cwd: str,
    title: str | None = None,
    model: str | None = None,
    messages: list[tuple[str, str]] | None = None,
    timestamp_ms: int | None = None,
) -> Path:
    """Build a synthetic codebuddy session jsonl mimicking the real format."""
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    lines: list[dict] = [
        {
            "id": "first-msg",
            "timestamp": ts,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first prompt"}],
            "sessionId": session_id,
            "cwd": cwd,
        }
    ]
    if title:
        lines.append(
            {
                "timestamp": ts + 100,
                "type": "ai-title",
                "aiTitle": title,
                "sessionId": session_id,
                "cwd": cwd,
            }
        )
    if messages:
        for i, (role, text) in enumerate(messages):
            entry = {
                "id": f"msg-{i}",
                "timestamp": ts + 200 + i,
                "type": "message",
                "role": role,
                "content": [
                    {"type": "input_text" if role == "user" else "output_text", "text": text}
                ],
                "sessionId": session_id,
                "cwd": cwd,
            }
            if role == "assistant" and model is not None:
                entry["providerData"] = {
                    "messageId": f"pm-{i}",
                    "model": "claude-opus-4-7",
                    "requestModelId": model,
                    "requestModelName": "Claude-Opus-4.7-1M",
                }
            lines.append(entry)
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


def _make_root(tmp_path: Path) -> Path:
    """Build a fake `~/.codebuddy/projects/` root."""
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# engine_id
# --------------------------------------------------------------------------- #


def test_engine_id() -> None:
    src = cb_src.CodeBuddyHandoffSource()
    assert src.engine_id == "codebuddy"


# --------------------------------------------------------------------------- #
# list_sessions
# --------------------------------------------------------------------------- #


def test_list_sessions_returns_recent_sorted_desc(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "old-session-aaaa",
        cwd="/Users/foo/Code/proj",
        title="Old work",
        timestamp_ms=1000,
    )
    _write_session_jsonl(
        project,
        "new-session-bbbb",
        cwd="/Users/foo/Code/proj",
        title="Recent work",
        timestamp_ms=5000,
    )

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    sessions = src.list_sessions(limit=10)

    assert len(sessions) == 2
    assert sessions[0].id == "new-session-bbbb"
    assert sessions[0].title == "Recent work"
    assert sessions[0].directory == "/Users/foo/Code/proj"
    assert sessions[1].id == "old-session-aaaa"


def test_list_sessions_respects_limit(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    for i in range(5):
        _write_session_jsonl(
            project,
            f"session-{i:02d}",
            cwd="/x",
            title=f"t{i}",
            timestamp_ms=1000 + i,
        )

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert len(src.list_sessions(limit=3)) == 3


def test_list_sessions_filters_by_cwd_when_provided(tmp_path: Path) -> None:
    """Sessions originating from a different cwd should be excluded when the
    caller scopes by cwd. This is what makes ``yee88 handoff`` show only the
    sessions for the current project by default.
    """
    root = _make_root(tmp_path)
    proj_a = root / "Users-foo-Code-A"
    proj_b = root / "Users-foo-Code-B"
    _write_session_jsonl(
        proj_a, "in-a", cwd="/Users/foo/Code/A", title="A work", timestamp_ms=2000
    )
    _write_session_jsonl(
        proj_b, "in-b", cwd="/Users/foo/Code/B", title="B work", timestamp_ms=3000
    )

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    sessions = src.list_sessions(cwd="/Users/foo/Code/A")
    assert [s.id for s in sessions] == ["in-a"]


def test_list_sessions_unfiltered_returns_all(tmp_path: Path) -> None:
    """Without ``cwd`` we get sessions from every project (legacy behavior)."""
    root = _make_root(tmp_path)
    proj_a = root / "Users-foo-Code-A"
    proj_b = root / "Users-foo-Code-B"
    _write_session_jsonl(proj_a, "in-a", cwd="/A", title="A", timestamp_ms=2000)
    _write_session_jsonl(proj_b, "in-b", cwd="/B", title="B", timestamp_ms=3000)

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    sessions = src.list_sessions()
    assert {s.id for s in sessions} == {"in-a", "in-b"}


def test_list_sessions_cwd_normalizes_trailing_slash(tmp_path: Path) -> None:
    """Filtering should be tolerant of trailing slashes on cwd."""
    root = _make_root(tmp_path)
    proj = root / "Users-foo-Code-A"
    _write_session_jsonl(proj, "x", cwd="/Users/foo/Code/A", title="A")

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert len(src.list_sessions(cwd="/Users/foo/Code/A/")) == 1
    assert len(src.list_sessions(cwd="/Users/foo/Code/A")) == 1


def test_list_sessions_no_projects_dir_returns_empty(tmp_path: Path) -> None:
    src = cb_src.CodeBuddyHandoffSource(projects_root=tmp_path / "nope")
    assert src.list_sessions() == []


def test_list_sessions_skips_invalid_files(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    project.mkdir(parents=True)
    # Empty file
    (project / "empty.jsonl").write_text("", encoding="utf-8")
    # Malformed
    (project / "bad.jsonl").write_text("not json\n", encoding="utf-8")
    # Valid
    _write_session_jsonl(project, "good-session", cwd="/x", title="ok")

    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    sessions = src.list_sessions()
    assert [s.id for s in sessions] == ["good-session"]


def test_list_sessions_falls_back_title_to_first_user_text_when_no_ai_title(
    tmp_path: Path,
) -> None:
    """If a session has no ai-title row, use the first user message as the title."""
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "untitled",
        cwd="/x",
        title=None,
        messages=None,
    )
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    sessions = src.list_sessions()
    assert sessions[0].id == "untitled"
    assert "first prompt" in sessions[0].title


# --------------------------------------------------------------------------- #
# get_messages
# --------------------------------------------------------------------------- #


def test_get_messages_returns_user_and_assistant_text(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "sid",
        cwd="/x",
        messages=[
            ("user", "Hello there"),
            ("assistant", "Hi back"),
            ("user", "More"),
            ("assistant", "Done"),
        ],
        model="claude-opus-4.7-1m",
    )
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    msgs = src.get_messages("sid", limit=3)
    # Last 3 in chronological order
    assert len(msgs) == 3
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[-1]["text"] == "Done"


def test_get_messages_unknown_session_returns_empty(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert src.get_messages("nope") == []


# --------------------------------------------------------------------------- #
# get_model_id
# --------------------------------------------------------------------------- #


def test_get_model_id_returns_request_model_id(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "sid-with-model",
        cwd="/x",
        messages=[("user", "hi"), ("assistant", "hello")],
        model="claude-opus-4.7-1m",
    )
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert src.get_model_id("sid-with-model") == "claude-opus-4.7-1m"


def test_get_model_id_returns_none_when_no_assistant_msg(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "sid-no-model",
        cwd="/x",
        messages=[("user", "hi")],
        model=None,
    )
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert src.get_model_id("sid-no-model") is None


def test_get_model_id_unknown_session_returns_none(tmp_path: Path) -> None:
    src = cb_src.CodeBuddyHandoffSource(projects_root=tmp_path / "nope")
    assert src.get_model_id("anything") is None


# --------------------------------------------------------------------------- #
# get_session_directory
# --------------------------------------------------------------------------- #


def test_get_session_directory_from_first_line_cwd(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    project = root / "Users-foo-Code-proj"
    _write_session_jsonl(
        project,
        "sid",
        cwd="/Users/foo/Code/proj",
        title="ok",
    )
    src = cb_src.CodeBuddyHandoffSource(projects_root=root)
    assert src.get_session_directory("sid") == "/Users/foo/Code/proj"


def test_get_session_directory_unknown_returns_none(tmp_path: Path) -> None:
    src = cb_src.CodeBuddyHandoffSource(projects_root=tmp_path / "nope")
    assert src.get_session_directory("x") is None


# --------------------------------------------------------------------------- #
# live: read the user's real ~/.codebuddy/projects directory
# --------------------------------------------------------------------------- #


@pytest.mark.live
def test_live_real_codebuddy_projects_dir() -> None:
    """Smoke test against the real local codebuddy storage."""
    real_root = Path.home() / ".codebuddy" / "projects"
    if not real_root.is_dir():
        pytest.skip("~/.codebuddy/projects does not exist")

    src = cb_src.CodeBuddyHandoffSource()
    sessions = src.list_sessions(limit=3)
    if not sessions:
        pytest.skip("no codebuddy sessions on this machine")

    top = sessions[0]
    assert top.id
    assert top.directory
    assert top.title
    # And we can walk into it
    assert src.get_session_directory(top.id) == top.directory
    msgs = src.get_messages(top.id, limit=2)
    # Most sessions should have at least one user msg
    assert all("role" in m and "text" in m for m in msgs)
