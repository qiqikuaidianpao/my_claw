"""Platform-agnostic ports (protocols) of the my_claw kernel.

The kernel depends only on these interfaces; host platforms (Dify today,
other hosts later) provide implementations in their adapter packages. This
is the seam that keeps SDK shapes from leaking into core — the biggest
structural risk identified in the mini_claw architecture audit.
"""
from __future__ import annotations

from typing import Any, Generator, Protocol, runtime_checkable

from core.llm import LLMRound


@runtime_checkable
class MessageEmitter(Protocol):
    """Sink for user-visible output events."""

    def text(self, chunk: str) -> None:
        """Emit one incremental piece of user-visible text."""

    def blob(self, data: bytes, *, mime_type: str, filename: str) -> None:
        """Emit one binary artifact (file attachment)."""

    def variable(self, key: str, value: dict[str, Any]) -> None:
        """Emit a structured variable (usage stats etc.)."""


@runtime_checkable
class KVStorage(Protocol):
    """Durable key-value storage scoped by the host."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...

    def delete(self, key: str) -> None: ...


@runtime_checkable
class LLMClient(Protocol):
    """One model invocation, streamed and buffered as a whole round."""

    def invoke_round(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMRound:
        """Run one streaming model call and return the buffered round."""

    def invoke_text(self, *, system: str, messages: list[Any]) -> str:
        """Non-streaming helper call returning plain text (extraction jobs)."""


@runtime_checkable
class UsageMeter(Protocol):
    """Token/cost/latency accounting across rounds."""

    def record_chunk(self, chunk: Any) -> None: ...

    def payload(self) -> dict[str, Any]: ...
