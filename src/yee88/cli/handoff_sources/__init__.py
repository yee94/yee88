"""Engine-agnostic abstractions for ``yee88 handoff send``.

The handoff command picks a recent session from the user's local AI agent
storage, summarizes it, and ships the context to a Telegram topic so the
conversation can continue from a phone. Each agent (opencode, codebuddy, …)
keeps sessions in its own format; this module defines the small protocol every
agent backend must satisfy plus the registry the CLI dispatches through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HandoffSessionInfo:
    """A user-visible summary of one local session."""

    id: str
    directory: str
    updated: float
    title: str

    @property
    def project_name(self) -> str:
        return Path(self.directory).name if self.directory else "unknown"

    @property
    def updated_str(self) -> str:
        return datetime.fromtimestamp(self.updated / 1000).strftime("%m-%d %H:%M")


class HandoffSource(Protocol):
    """A pluggable source of handoff data for one engine."""

    @property
    def engine_id(self) -> str: ...

    def list_sessions(
        self, limit: int = 10, *, cwd: str | None = None
    ) -> list[HandoffSessionInfo]:
        """Return the most recently-touched sessions, newest first.

        When ``cwd`` is provided, only sessions whose originating working
        directory matches it are returned. This lets the CLI scope the
        picker to the project the user is currently in.
        """
        ...

    def get_messages(self, session_id: str, limit: int = 5) -> list[dict]:
        """Return the last ``limit`` user/assistant text messages, oldest first."""
        ...

    def get_model_id(self, session_id: str) -> str | None:
        """Return a CLI-compatible model identifier for that session, if known."""
        ...

    def get_session_directory(self, session_id: str) -> str | None:
        """Return the cwd the session was started from, if known."""
        ...
