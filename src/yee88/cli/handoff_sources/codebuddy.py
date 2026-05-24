"""CodeBuddy handoff source.

CodeBuddy stores one JSONL file per session under
``~/.codebuddy/projects/<encoded-cwd>/<session-uuid>.jsonl``. Each line is a
self-describing event — see ``schemas/codebuddy.py`` for the runtime view; for
handoff we only need:

- first line: ``type=message`` ``role=user`` with ``cwd`` and ``timestamp``
- title: ``type=ai-title`` (optional; otherwise we synthesize from first user msg)
- assistant turns: ``providerData.requestModelId`` carries the CLI model ID
- text content: ``content[].text`` for both user and assistant messages
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import HandoffSessionInfo


DEFAULT_PROJECTS_ROOT = Path.home() / ".codebuddy" / "projects"


@dataclass(slots=True)
class CodeBuddyHandoffSource:
    """Read handoff data from CodeBuddy's local session storage."""

    projects_root: Path = DEFAULT_PROJECTS_ROOT

    @property
    def engine_id(self) -> str:
        return "codebuddy"

    # --------------------------------------------------------------------- #
    # public API
    # --------------------------------------------------------------------- #

    def list_sessions(
        self, limit: int = 10, *, cwd: str | None = None
    ) -> list[HandoffSessionInfo]:
        if not self.projects_root.is_dir():
            return []

        cwd_filter = _normalize_cwd(cwd) if cwd else None

        candidates: list[HandoffSessionInfo] = []
        for jsonl_path in self.projects_root.glob("*/*.jsonl"):
            info = self._load_session_summary(jsonl_path)
            if info is None:
                continue
            if cwd_filter is not None and _normalize_cwd(info.directory) != cwd_filter:
                continue
            candidates.append(info)

        candidates.sort(key=lambda s: s.updated, reverse=True)
        return candidates[:limit]

    def get_messages(self, session_id: str, limit: int = 5) -> list[dict]:
        path = self._find_session_file(session_id)
        if path is None:
            return []
        messages: list[dict] = []
        for line in self._iter_lines(path):
            if line.get("type") != "message":
                continue
            role = line.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(line.get("content"))
            if not text:
                continue
            messages.append({"role": role, "text": text})
        # Keep the last `limit` in chronological order.
        return messages[-limit:]

    def get_model_id(self, session_id: str) -> str | None:
        path = self._find_session_file(session_id)
        if path is None:
            return None
        latest_model: str | None = None
        for line in self._iter_lines(path):
            if line.get("type") != "message" or line.get("role") != "assistant":
                continue
            provider = line.get("providerData") or {}
            request_model = provider.get("requestModelId")
            if isinstance(request_model, str) and request_model:
                latest_model = request_model
        return latest_model

    def get_session_directory(self, session_id: str) -> str | None:
        path = self._find_session_file(session_id)
        if path is None:
            return None
        for line in self._iter_lines(path):
            cwd = line.get("cwd")
            if isinstance(cwd, str) and cwd:
                return cwd
            # First line typically has cwd; bail if we already saw a non-cwd row
            break
        return None

    # --------------------------------------------------------------------- #
    # internals
    # --------------------------------------------------------------------- #

    def _find_session_file(self, session_id: str) -> Path | None:
        if not self.projects_root.is_dir():
            return None
        for project_dir in self.projects_root.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
        return None

    def _load_session_summary(self, path: Path) -> HandoffSessionInfo | None:
        session_id = path.stem
        cwd: str | None = None
        title: str | None = None
        first_user_text: str | None = None
        first_timestamp: int | None = None
        latest_timestamp: int | None = None

        for line in self._iter_lines(path):
            ts = line.get("timestamp")
            if isinstance(ts, int):
                if first_timestamp is None:
                    first_timestamp = ts
                latest_timestamp = ts

            if cwd is None:
                line_cwd = line.get("cwd")
                if isinstance(line_cwd, str) and line_cwd:
                    cwd = line_cwd

            ltype = line.get("type")
            if ltype == "ai-title":
                ai_title = line.get("aiTitle")
                if isinstance(ai_title, str) and ai_title:
                    title = ai_title
            elif ltype == "message" and first_user_text is None and line.get("role") == "user":
                text = _extract_text(line.get("content"))
                if text:
                    first_user_text = text

        if first_timestamp is None or cwd is None:
            return None

        display_title = title or _truncate(first_user_text or session_id, 60)
        return HandoffSessionInfo(
            id=session_id,
            directory=cwd,
            updated=float(latest_timestamp or first_timestamp),
            title=display_title,
        )

    @staticmethod
    def _iter_lines(path: Path):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return


def _extract_text(content) -> str:
    """Pull the first text fragment from a codebuddy ``content`` array."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            return text
    return ""


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _normalize_cwd(value: str) -> str:
    """Strip trailing slashes for tolerant cwd comparison."""
    return value.rstrip("/") or "/"
