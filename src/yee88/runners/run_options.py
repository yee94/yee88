from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineRunOptions:
    model: str | None = None
    reasoning: str | None = None
    system: str | None = None


# ---------------------------------------------------------------------------
# Runtime environment variables injected by the transport layer.
# These are merged into the subprocess env by JsonlSubprocessRunner.env().
# ---------------------------------------------------------------------------

_RUNTIME_ENV: ContextVar[dict[str, str] | None] = ContextVar(
    "yee88.runtime_env", default=None
)


def get_runtime_env() -> dict[str, str] | None:
    return _RUNTIME_ENV.get()


def set_runtime_env(env: dict[str, str] | None) -> Token:
    return _RUNTIME_ENV.set(env)


def reset_runtime_env(token: Token) -> None:
    _RUNTIME_ENV.reset(token)


@contextmanager
def apply_runtime_env(env: dict[str, str] | None) -> Iterator[None]:
    token = set_runtime_env(env)
    try:
        yield
    finally:
        reset_runtime_env(token)

_RUN_OPTIONS: ContextVar[EngineRunOptions | None] = ContextVar(
    "yee88.engine_run_options", default=None
)


def get_run_options() -> EngineRunOptions | None:
    return _RUN_OPTIONS.get()


def set_run_options(options: EngineRunOptions | None) -> Token:
    return _RUN_OPTIONS.set(options)


def reset_run_options(token: Token) -> None:
    _RUN_OPTIONS.reset(token)


@contextmanager
def apply_run_options(options: EngineRunOptions | None) -> Iterator[None]:
    token = set_run_options(options)
    try:
        yield
    finally:
        reset_run_options(token)
