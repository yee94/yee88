from __future__ import annotations

from pathlib import Path
import tomllib

from yee88.cli import handoff


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
