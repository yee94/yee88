"""OpenCode handoff source — preserves the historical behavior of ``yee88 handoff``.

This wraps the OpenCode-specific lookup helpers (sqlite + filesystem fallback)
behind the :class:`HandoffSource` protocol so the CLI can dispatch by engine.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import HandoffSessionInfo


DEFAULT_OPENCODE_STORAGE = Path.home() / ".local" / "share" / "opencode" / "storage"
DEFAULT_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@dataclass(slots=True)
class OpenCodeHandoffSource:
    """Read handoff data from OpenCode's local session storage."""

    storage: Path = DEFAULT_OPENCODE_STORAGE
    db_path: Path = DEFAULT_OPENCODE_DB

    @property
    def engine_id(self) -> str:
        return "opencode"

    def list_sessions(
        self, limit: int = 10, *, cwd: str | None = None
    ) -> list[HandoffSessionInfo]:
        try:
            result = subprocess.run(
                ["opencode", "session", "list", "--format", "json", "-n", str(limit)],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)
            sessions = [
                HandoffSessionInfo(
                    id=s.get("id", ""),
                    directory=s.get("directory", ""),
                    updated=s.get("updated", 0),
                    title=s.get("title", ""),
                )
                for s in data
            ]
            if cwd is not None:
                cwd_norm = cwd.rstrip("/") or "/"
                sessions = [
                    s for s in sessions if (s.directory.rstrip("/") or "/") == cwd_norm
                ]
            return sessions
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            return []

    def get_messages(self, session_id: str, limit: int = 5) -> list[dict]:
        if self.db_path.exists():
            result = self._messages_from_sqlite(session_id, limit)
            if result:
                return result
        return self._messages_from_fs(session_id, limit)

    def get_model_id(self, session_id: str) -> str | None:
        if not self.db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data FROM message "
                "WHERE session_id = ? "
                "ORDER BY time_created DESC LIMIT 20",
                (session_id,),
            )
            for row in cursor.fetchall():
                msg_data = json.loads(row["data"])
                model_id = msg_data.get("modelID")
                if model_id and msg_data.get("role") == "assistant":
                    provider_id = msg_data.get("providerID")
                    conn.close()
                    if provider_id:
                        return f"{provider_id}/{model_id}"
                    return model_id
            conn.close()
        except (sqlite3.Error, json.JSONDecodeError, OSError):
            pass
        return None

    def get_session_directory(self, session_id: str) -> str | None:
        # OpenCode CLI's session list already supplies directory; we don't need
        # a separate lookup in normal flow. Read it from sqlite if requested.
        if not self.db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data FROM session WHERE id = ?", (session_id,)
            )
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return None
            data = json.loads(row["data"])
            value = data.get("directory")
            return value if isinstance(value, str) else None
        except (sqlite3.Error, json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _messages_from_sqlite(self, session_id: str, limit: int) -> list[dict]:
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, data FROM message "
                "WHERE session_id = ? "
                "ORDER BY time_created DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()
            rows.reverse()

            result: list[dict] = []
            for row in rows:
                msg_data = json.loads(row["data"])
                role = msg_data.get("role", "unknown")
                msg_id = row["id"]
                cursor.execute(
                    "SELECT data FROM part WHERE message_id = ? ORDER BY time_created ASC",
                    (msg_id,),
                )
                for part_row in cursor.fetchall():
                    part_data = json.loads(part_row["data"])
                    if part_data.get("type") == "text":
                        text = part_data.get("text", "")
                        result.append({"role": role, "text": text})
                        break
            conn.close()
            return result
        except (sqlite3.Error, json.JSONDecodeError, OSError):
            return []

    def _messages_from_fs(self, session_id: str, limit: int) -> list[dict]:
        message_dir = self.storage / "message" / session_id
        if not message_dir.exists():
            return []

        messages: list[tuple[int, str, str]] = []
        for msg_file in message_dir.glob("msg_*.json"):
            try:
                data = json.loads(msg_file.read_text())
                created = data.get("time", {}).get("created", 0)
                role = data.get("role", "unknown")
                msg_id = data.get("id", "")
                messages.append((created, role, msg_id))
            except (json.JSONDecodeError, OSError):
                continue

        messages.sort(key=lambda x: x[0], reverse=True)
        messages = messages[:limit]
        messages.reverse()

        result: list[dict] = []
        for _, role, msg_id in messages:
            part_dir = self.storage / "part" / msg_id
            if not part_dir.exists():
                continue
            for part_file in part_dir.glob("prt_*.json"):
                try:
                    part_data = json.loads(part_file.read_text())
                    if part_data.get("type") == "text":
                        text = part_data.get("text", "")
                        if text.startswith('"') and text.endswith('"\n'):
                            text = json.loads(text.rstrip("\n"))
                        result.append({"role": role, "text": text})
                        break
                except (json.JSONDecodeError, OSError):
                    continue
        return result
