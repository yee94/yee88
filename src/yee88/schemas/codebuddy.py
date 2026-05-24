"""Msgspec models and decoder for CodeBuddy Code stream-json output.

CodeBuddy Code's stream-json schema is a near-exact superset of Claude Code's:
it adds two event kinds that Claude does not emit:

- ``system.subtype="status"`` rows (status pings; safely ignored at translate time)
- ``file-history-snapshot`` rows (file backup tracking)

Everything else (assistant/user/result/tool_use/tool_result/text/thinking) is
structurally identical, so we reuse the block types from ``schemas.claude``.
"""

from __future__ import annotations

from typing import Any

import msgspec

from .claude import (
    StreamAssistantMessage,
    StreamAssistantMessageBody,
    StreamContentBlock,
    StreamResultMessage,
    StreamSystemMessage,
    StreamTextBlock,
    StreamThinkingBlock,
    StreamToolResultBlock,
    StreamToolUseBlock,
    StreamUserMessage,
    StreamUserMessageBody,
)


class FileHistorySnapshot(
    msgspec.Struct,
    tag="file-history-snapshot",
    tag_field="type",
    forbid_unknown_fields=False,
):
    """CodeBuddy-only event. Tracks file backups for the rewind feature.

    Translation layer ignores these rows; they are decoded only so the JSONL
    stream parses cleanly without dropping into the error path.
    """

    id: str
    timestamp: int
    isSnapshotUpdate: bool
    snapshot: dict[str, Any] | None = None


type StreamJsonMessage = (
    StreamUserMessage
    | StreamAssistantMessage
    | StreamSystemMessage
    | StreamResultMessage
    | FileHistorySnapshot
)


_DECODER = msgspec.json.Decoder(StreamJsonMessage)


def decode_stream_json_line(line: str | bytes) -> StreamJsonMessage:
    return _DECODER.decode(line)


__all__ = [
    "FileHistorySnapshot",
    "StreamAssistantMessage",
    "StreamAssistantMessageBody",
    "StreamContentBlock",
    "StreamJsonMessage",
    "StreamResultMessage",
    "StreamSystemMessage",
    "StreamTextBlock",
    "StreamThinkingBlock",
    "StreamToolResultBlock",
    "StreamToolUseBlock",
    "StreamUserMessage",
    "StreamUserMessageBody",
    "decode_stream_json_line",
]
