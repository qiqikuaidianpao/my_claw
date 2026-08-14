"""Dify adapter: MessageEmitter + KVStorage over a plugin Tool instance."""
from __future__ import annotations

from typing import Any


class DifyMessageEmitter:
    """Bridges core.ports.MessageEmitter onto a Tool's create_*_message API.

    Text is yielded in small chunks through the tool's generator so the host
    streams it as if typed; blobs and variables map one-to-one.
    """

    def __init__(self, tool: Any, emit) -> None:
        # emit: callable that yields an already-created message into the run
        self._tool = tool
        self._emit = emit

    def text(self, chunk: str) -> None:
        if chunk:
            self._emit(self._tool.create_text_message(chunk))

    def blob(self, data: bytes, *, mime_type: str, filename: str) -> None:
        self._emit(self._tool.create_blob_message(blob=data, meta={"mime_type": mime_type, "filename": filename}))

    def variable(self, key: str, value: dict[str, Any]) -> None:
        self._emit(self._tool.create_variable_message(key, value))


class DifyKVStorage:
    """Implements core.ports.KVStorage over session.storage (bytes values)."""

    def __init__(self, session: Any) -> None:
        self._storage = session.storage

    def get(self, key: str) -> bytes | None:
        try:
            value = self._storage.get(key)
            if value is None:
                return None
            return value if isinstance(value, bytes) else str(value).encode("utf-8")
        except Exception:
            return None

    def set(self, key: str, value: bytes) -> None:
        self._storage.set(key, value)

    def delete(self, key: str) -> None:
        try:
            self._storage.delete(key)
        except Exception:
            pass
