"""Tests for codebuddy-specific helpers in /model command.

Covers:
- ``_get_codebuddy_models``: parsing ``codebuddy --help`` output
- ``_apply_model_filter``: regex include/exclude (already tested implicitly via
  opencode flow, but we add codebuddy-shaped scenarios)
- naming sanity: helper exists and returns the expected list shape
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from yee88.telegram.commands.model import (
    _apply_model_filter,
    _get_codebuddy_models,
)


# Real fragment of `codebuddy --help` (truncated for the test) — keep in sync
# if codebuddy ever reformats its help output.
HELP_SAMPLE = (
    "Options:\n"
    "  -V, --version                                    output the version number\n"
    "  --model <model>                                  Model for the current session. "
    "Please provide the model ID. Currently supported: ("
    "claude-sonnet-4.6, claude-sonnet-4.6-1m, claude-4.5, claude-opus-4.7, "
    "claude-opus-4.7-1m, claude-opus-4.6, claude-opus-4.6-1m, claude-opus-4.5, "
    "claude-haiku-4.5, gemini-3.1-pro, gemini-3.0-flash, gemini-3.5-flash, "
    "gemini-2.5-pro, gemini-3.1-flash-lite, gpt-5.5, gpt-5.4, gpt-5.3-codex, "
    "gpt-5.1-codex, gpt-5.1-codex-mini, glm-5.1-ioa, glm-5.0-turbo-ioa, "
    "glm-5v-turbo-ioa, glm-5.0-ioa, glm-4.7-ioa, minimax-m2.7-ioa, minimax-m2.5-ioa, "
    "kimi-k2.6-ioa, kimi-k2.5-ioa, hy3-preview-ioa, deepseek-v3-2-volc-ioa)\n"
    "  --text-to-image-model <model>                    Model for text-to-image generation\n"
)

EXPECTED_MODELS = [
    "claude-sonnet-4.6",
    "claude-sonnet-4.6-1m",
    "claude-4.5",
    "claude-opus-4.7",
    "claude-opus-4.7-1m",
    "claude-opus-4.6",
    "claude-opus-4.6-1m",
    "claude-opus-4.5",
    "claude-haiku-4.5",
    "gemini-3.1-pro",
    "gemini-3.0-flash",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "gemini-3.1-flash-lite",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "glm-5.1-ioa",
    "glm-5.0-turbo-ioa",
    "glm-5v-turbo-ioa",
    "glm-5.0-ioa",
    "glm-4.7-ioa",
    "minimax-m2.7-ioa",
    "minimax-m2.5-ioa",
    "kimi-k2.6-ioa",
    "kimi-k2.5-ioa",
    "hy3-preview-ioa",
    "deepseek-v3-2-volc-ioa",
]


def _fake_subprocess_factory(stdout: bytes, returncode: int = 0):
    """Build a coroutine that mocks ``asyncio.create_subprocess_exec``."""

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return stdout, b""

    async def _factory(*_args, **_kwargs):
        return _FakeProc()

    return _factory


@pytest.mark.anyio
async def test_get_codebuddy_models_parses_help_output() -> None:
    with patch(
        "asyncio.create_subprocess_exec",
        new=_fake_subprocess_factory(HELP_SAMPLE.encode()),
    ):
        models = await _get_codebuddy_models()
    assert models == EXPECTED_MODELS


@pytest.mark.anyio
async def test_get_codebuddy_models_returns_empty_on_nonzero_rc() -> None:
    with patch(
        "asyncio.create_subprocess_exec",
        new=_fake_subprocess_factory(b"", returncode=1),
    ):
        models = await _get_codebuddy_models()
    assert models == []


@pytest.mark.anyio
async def test_get_codebuddy_models_returns_empty_when_no_match() -> None:
    """If --help output ever changes its phrasing, return empty (don't raise)."""
    with patch(
        "asyncio.create_subprocess_exec",
        new=_fake_subprocess_factory(b"unrelated help text without the marker\n"),
    ):
        models = await _get_codebuddy_models()
    assert models == []


@pytest.mark.anyio
async def test_get_codebuddy_models_handles_subprocess_exception() -> None:
    """If codebuddy binary is not on PATH, return empty silently."""

    async def _raise(*_args, **_kwargs):
        raise FileNotFoundError("codebuddy not found")

    with patch("asyncio.create_subprocess_exec", new=_raise):
        models = await _get_codebuddy_models()
    assert models == []


def test_apply_model_filter_keeps_claude_only() -> None:
    """Smoke-check filter syntax for codebuddy-shaped lists."""
    filtered = _apply_model_filter(EXPECTED_MODELS, "claude")
    assert all("claude" in m for m in filtered)
    assert "gpt-5.5" not in filtered


def test_apply_model_filter_excludes_codex_and_image_models() -> None:
    filtered = _apply_model_filter(EXPECTED_MODELS, "!codex|!ioa")
    assert "gpt-5.3-codex" not in filtered
    assert "kimi-k2.6-ioa" not in filtered
    assert "claude-opus-4.7" in filtered


def test_apply_model_filter_combination() -> None:
    filtered = _apply_model_filter(
        EXPECTED_MODELS, "claude|gemini|!4\\.6$"
    )
    # include: claude, gemini; exclude: anything ending in `-4.6` (no 1m suffix)
    assert "claude-sonnet-4.6" not in filtered
    assert "claude-sonnet-4.6-1m" in filtered
    assert "gemini-3.1-pro" in filtered
    assert "gpt-5.5" not in filtered


# --------------------------------------------------------------------------- #
# Live smoke: actually run `codebuddy --help` and check we extract a non-empty
# list. Skipped by default; run with `pytest -m live`.
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.anyio
async def test_get_codebuddy_models_live() -> None:
    import shutil as _shutil

    if _shutil.which("codebuddy") is None:
        pytest.skip("codebuddy binary not on PATH")

    models = await _get_codebuddy_models()
    assert len(models) > 0, "live codebuddy --help yielded no models"
    # Sanity: the canonical claude-opus-4.7-1m id should be present (it's the
    # current default model). If codebuddy ever drops it, this test will flag it.
    assert any("claude-opus" in m for m in models), models
