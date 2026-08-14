"""SessionContext — one explicit state object per agent invocation.

Replaces the ~12 mutable closure variables that entangled mini_claw's
2258-line god method (messages / final_text / streamed flags / loop
histories / approval state scattered across 1400 lines).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.llm import LLMRound


@dataclass
class LoopGuard:
    """Repetition detection over tool-call signatures."""

    window: int = 30
    warn_after: int = 10
    stop_after: int = 20
    no_progress_limit: int = 30
    _sigs: list[str] = field(default_factory=list)
    _sig_results: list[tuple[str, str]] = field(default_factory=list)
    _warned: set[str] = field(default_factory=set)

    def observe(self, sig: str, result_digest: str) -> tuple[bool, bool]:
        """Record one call; returns (warned_now, should_stop)."""
        self._sigs.append(sig)
        if len(self._sigs) > 400:
            self._sigs = self._sigs[-200:]
        self._sig_results.append((sig, result_digest))
        if len(self._sig_results) > 400:
            self._sig_results = self._sig_results[-200:]
        recent = self._sigs[-self.window:]
        repeats = sum(1 for s in recent if s == sig)
        warned_now = False
        if repeats >= self.warn_after and sig not in self._warned:
            self._warned.add(sig)
            warned_now = True
        should_stop = repeats >= self.stop_after
        if not should_stop and self.no_progress_limit > 0:
            pairs = self._sig_results[-self.no_progress_limit:]
            if len(pairs) >= self.no_progress_limit and len(set(pairs)) == 1:
                should_stop = True
        return warned_now, should_stop


@dataclass
class Artifact:
    """A declared deliverable inside the session workspace."""

    relative_path: str
    filename: str
    mime_type: str
    registered: bool = False


@dataclass
class SessionContext:
    """Everything one agent run needs, injectable and unit-testable."""

    # identity / scoping
    session_id: str
    app_id: str = ""
    user_id: str = ""
    conversation_id: str = ""

    # workspace roots (writable session dir; read-only skills dir)
    workspace_root: str = ""
    skills_root: str = ""

    # conversation state
    messages: list[Any] = field(default_factory=list)
    system_prompt: str = ""
    summary: str = ""  # compaction summary of dropped history

    # budget / limits
    started_at: float = field(default_factory=time.time)
    timeout_seconds: int = 600
    max_tool_turns: int = 50

    # delivery state
    final_text: str = ""
    final_text_emitted: bool = False
    artifacts: dict[str, Artifact] = field(default_factory=dict)

    # loop safety
    loop_guard: LoopGuard = field(default_factory=LoopGuard)

    # misc counters / extension bag (persona, approval hooks...)
    empty_responses: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    rounds: list[LLMRound] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    @property
    def timed_out(self) -> bool:
        return self.timeout_seconds > 0 and self.elapsed > self.timeout_seconds

    def register_artifact(self, rel_path: str, *, filename: str | None = None, mime_type: str = "") -> None:
        from core.util import guess_mime_type

        name = filename or rel_path.replace("\\", "/").rsplit("/", 1)[-1]
        self.artifacts[rel_path] = Artifact(
            relative_path=rel_path, filename=name, mime_type=mime_type or guess_mime_type(name), registered=True
        )

    def snapshot_state(self) -> dict[str, Any]:
        """Diagnostic snapshot (for tests and structured logs)."""
        return {
            "session_id": self.session_id,
            "messages": len(self.messages),
            "rounds": len(self.rounds),
            "elapsed": round(self.elapsed, 1),
            "artifacts": len(self.artifacts),
            "empty_responses": self.empty_responses,
        }
