"""ContextManager — token budget, history persistence and compaction.

Explicitly extracted from mini_claw's inline ``compact_if_needed`` closure:
token estimation, summarize-when-over-budget (with memory extraction into
the persona's managed block), and conversation history persistence.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from core import log
from core.session import SessionContext

HISTORY_TURNS = 50


class KVLike(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...


class LLMTextLike(Protocol):
    def invoke_text(self, *, system: str, messages: list[Any]) -> str: ...


def estimate_tokens(text: str) -> int:
    """Same heuristic as mini_claw: chars//4 is close enough for CJK+EN mix."""
    return len(text or "") // 4


def estimate_messages_tokens(messages: list[Any]) -> int:
    total = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        total += estimate_tokens(str(content or ""))
        for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            total += estimate_tokens(str(tc.get("function", {}).get("arguments") or ""))
    return total


class ContextManager:
    def __init__(
        self,
        *,
        llm: LLMTextLike | None = None,
        persona_merge=None,
        budget_tokens: int = 12000,
        keep_recent_tokens: int = 2500,
    ) -> None:
        self.llm = llm
        self.persona_merge = persona_merge  # callable(dict) for memory extraction
        self.budget = budget_tokens
        self.keep_recent = keep_recent_tokens

    def compact_if_needed(self, ctx: SessionContext) -> None:
        est = estimate_messages_tokens(ctx.messages)
        if est <= self.budget:
            return
        self._compact(ctx, est)

    def _compact(self, ctx: SessionContext, est: int) -> None:
        # walk from the end keeping a recent tail under keep_recent tokens
        cut = len(ctx.messages)
        kept = 0
        for i in range(len(ctx.messages) - 1, -1, -1):
            m = ctx.messages[i]
            kept += estimate_tokens(str(m.get("content") or ""))
            cut = i
            if kept >= self.keep_recent:
                break
        if cut <= 0:
            return
        older = ctx.messages[:cut]
        ctx.messages = ctx.messages[cut:]
        summary = self._summarize(older)
        if summary:
            ctx.summary = (ctx.summary + "\n" + summary).strip() if ctx.summary else summary
            ctx.messages.insert(0, {"role": "user", "content": f"[此前对话摘要]\n{ctx.summary}"})
        log.info("context_compacted", dropped=len(older), est_before=est, est_after=estimate_messages_tokens(ctx.messages))

    def _summarize(self, older: list[Any]) -> str:
        if not self.llm:
            # fallback: keep first lines of each message (rare, keeps agent alive)
            lines = []
            for m in older:
                c = str(m.get("content") or "").strip().splitlines()
                if c:
                    lines.append(c[0][:120])
            return "历史概要(降级)：" + " / ".join(lines[:20])
        try:
            transcript = "\n".join(
                f"{m.get('role')}: {str(m.get('content') or '')[:500]}" for m in older[-30:]
            )
            summary = self.llm.invoke_text(
                system="你是对话压缩器。用不超过300字总结以下对话的关键事实、决定与未完成事项，只输出摘要。",
                messages=[{"role": "user", "content": transcript}],
            )
            self._extract_memory(transcript)
            return summary.strip()
        except Exception as e:
            log.warning("compact_summarize_failed", detail=str(e))
            return ""

    def _extract_memory(self, transcript: str) -> None:
        if self.persona_merge is None or self.llm is None:
            return
        try:
            raw = self.llm.invoke_text(
                system=(
                    "从对话中提取长期记忆。只输出JSON对象，键为 user_preferences/project_facts/decisions，"
                    "值为字符串数组（每条不超60字）。没有可提取的就输出 {}。"
                ),
                messages=[{"role": "user", "content": transcript[:6000]}],
            )
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                return
            data = json.loads(raw[start : end + 1])
            updates = {k: [str(x) for x in v] for k, v in data.items() if isinstance(v, list) and v}
            if updates:
                self.persona_merge(updates)
        except Exception as e:
            log.debug("memory_extract_skipped", detail=str(e))


# ── history persistence ─────────────────────────────────────────────────


class HistoryStore:
    """Append-only JSON turn log per conversation (cross-invocation)."""

    def __init__(self, kv: KVLike, *, conversation_id: str) -> None:
        self.kv = kv
        self.key = f"claw:history:{conversation_id or 'anonymous'}"

    def load(self) -> list[dict[str, Any]]:
        raw = self.kv.get(self.key)
        if not raw:
            return []
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def append(self, user_text: str, assistant_text: str) -> None:
        turns = self.load()
        turns.append({"u": user_text[:2000], "a": assistant_text[:2000]})
        self.kv.set(self.key, json.dumps(turns[-HISTORY_TURNS:], ensure_ascii=False).encode("utf-8"))
