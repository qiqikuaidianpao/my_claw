"""Structured, redacted logging for the my_claw kernel.

Replaces mini_claw's bare ``print("[skill][debug] ...")`` with leveled,
field-based logging. Chunk diagnostics follow the "sanitized chunk log"
contract from the streaming design doc: lengths and flags only, never user
content, prompts, keys or file bodies.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

_LOGGER_NAME = "my_claw"

_LOGGER = logging.getLogger(_LOGGER_NAME)
if not _LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [my_claw] %(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False

# Fields that must never be logged verbatim.
_REDACT_KEYS = {"api_key", "authorization", "cookie", "token", "secret", "password"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in _REDACT_KEYS else _redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _log(level: int, event: str, **fields: Any) -> None:
    if _LOGGER.isEnabledFor(level):
        payload = json.dumps(_redact(fields), ensure_ascii=False, default=str)
        _LOGGER.log(level, f"{event} {payload}")


def debug(event: str, **fields: Any) -> None:
    _log(logging.DEBUG, event, **fields)


def info(event: str, **fields: Any) -> None:
    _log(logging.INFO, event, **fields)


def warning(event: str, **fields: Any) -> None:
    _log(logging.WARNING, event, **fields)


def error(event: str, **fields: Any) -> None:
    _log(logging.ERROR, event, **fields)


def chunk_diag(round_no: int, chunk_no: int, *, content_len: int, reasoning_len: int, tool_calls: int) -> None:
    """Sanitized per-chunk diagnostics for streaming debugging (no content)."""
    debug(
        "llm_chunk",
        round=round_no,
        chunk=chunk_no,
        content_len=content_len,
        reasoning_len=reasoning_len,
        tool_calls=tool_calls,
    )
