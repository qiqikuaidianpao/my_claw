"""MemoryService — daily digests and cross-session recall.

Persists a per-day dialogue digest and injects today+yesterday into the
system prompt, giving the agent cross-session memory without embedding the
full history. Ported from mini_claw's memory module with GC and a size cap.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

from core import log

CST = timezone(timedelta(hours=8))  # company timezone, same as mini_claw
MAX_DAY_CHARS = 20000
KEEP_DAYS = 30


class KVLike(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...


def _now_cst() -> datetime:
    return datetime.now(CST)


class MemoryService:
    def __init__(self, kv: KVLike, *, app_id: str, user_id: str = "") -> None:
        self.kv = kv
        self.app_id = app_id or "app"
        self.user_id = user_id or ""

    def _day_key(self, day: str) -> str:
        scope = f":user:{self.user_id}" if self.user_id else ""
        return f"claw:memory:{self.app_id}{scope}:{day}.md"

    # ── digests ──────────────────────────────────────────────────────────

    def append_digest(self, user_text: str, assistant_text: str) -> None:
        day = _now_cst().strftime("%Y-%m-%d")
        key = self._day_key(day)
        raw = self.kv.get(key)
        body = raw.decode("utf-8", errors="ignore") if raw else f"# {day} 对话记录\n"
        entry = f"\n## {_now_cst().strftime('%H:%M')}\n- 用户: {self._clip(user_text)}\n- 助手: {self._clip(assistant_text)}\n"
        body = (body + entry)[:MAX_DAY_CHARS]
        self.kv.set(key, body.encode("utf-8"))

    @staticmethod
    def _clip(text: str, limit: int = 600) -> str:
        s = re.sub(r"\s+", " ", str(text or "")).strip()
        return s[:limit]

    # ── recall ───────────────────────────────────────────────────────────

    def recent_context(self, *, days: int = 2, max_chars: int = 2500) -> str:
        parts: list[str] = []
        for offset in range(days):
            day = (_now_cst() - timedelta(days=offset)).strftime("%Y-%m-%d")
            raw = self.kv.get(self._day_key(day))
            if raw:
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    parts.append(f"[{day} 记录]\n{text}")
        if not parts:
            return ""
        return "\n\n".join(parts)[:max_chars]

    # ── GC ───────────────────────────────────────────────────────────────

    def gc(self, *, force: bool = False) -> int:
        """Drop digest files older than KEEP_DAYS.

        Cheap marker-based scan: without list capabilities in the KV port we
        probe the fixed window (KEEP_DAYS*2 days back) at most once per day.
        """
        marker_key = f"claw:memory:{self.app_id}:gc_marker"
        today = _now_cst().strftime("%Y-%m-%d")
        if not force:
            marker = self.kv.get(marker_key)
            if marker and marker.decode("utf-8", errors="ignore") == today:
                return 0
        removed = 0
        for offset in range(KEEP_DAYS, KEEP_DAYS * 12):
            day = (_now_cst() - timedelta(days=offset)).strftime("%Y-%m-%d")
            key = self._day_key(day)
            if self.kv.get(key) is not None:
                try:
                    self.kv.set(key, b"")
                    removed += 1
                except Exception as e:
                    log.warning("memory_gc_failed", day=day, detail=str(e))
        self.kv.set(marker_key, today.encode("utf-8"))
        return removed
