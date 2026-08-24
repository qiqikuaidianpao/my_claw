"""PersonaStore — the four-document persona model, modularized.

IDENTITY.md / USER.md / SOUL.md / MEMORY.md live in KV storage, app-scoped
(with per-user override for USER.md / MEMORY.md). Extracted from mini_claw's
220-line inline persona closures into a testable service.
"""
from __future__ import annotations

from typing import Protocol

from core import log

DOC_NAMES = ("IDENTITY.md", "USER.md", "SOUL.md", "MEMORY.md")
MANAGED_HEADER = "## Managed Memory (auto)"
MEMORY_FAMILY = ("MEMORY.md", "MEMORY.bak.md", "MEMORY.archive.md")  # user-scoped like MEMORY.md


class KVLike(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...


class PersonaStore:
    def __init__(self, kv: KVLike, *, app_id: str, user_id: str = "") -> None:
        self.kv = kv
        self.app_id = app_id or "app"
        self.user_id = user_id or ""

    # ── keys ─────────────────────────────────────────────────────────────

    def _key(self, name: str, *, user_scoped: bool = False) -> str:
        if user_scoped and self.user_id:
            return f"claw:persona:{self.app_id}:user:{self.user_id}:{name}"
        return f"claw:persona:{self.app_id}:{name}"

    # ── docs ─────────────────────────────────────────────────────────────

    def read(self, name: str) -> str:
        if name not in DOC_NAMES and name not in MEMORY_FAMILY:
            raise ValueError(f"unknown persona doc: {name}")
        user_scoped = name in ("USER.md",) or name in MEMORY_FAMILY
        raw = self.kv.get(self._key(name, user_scoped=user_scoped))
        if raw is None and user_scoped:
            raw = self.kv.get(self._key(name, user_scoped=False))
        return raw.decode("utf-8", errors="ignore") if raw else ""

    def write(self, name: str, content: str) -> None:
        if name not in DOC_NAMES and name not in MEMORY_FAMILY:
            raise ValueError(f"unknown persona doc: {name}")
        user_scoped = name in ("USER.md",) or name in MEMORY_FAMILY
        self.kv.set(self._key(name, user_scoped=user_scoped), content.encode("utf-8"))

    def reset(self, *, keep_identity: bool = False) -> None:
        for name in DOC_NAMES + MEMORY_FAMILY:
            if keep_identity and name == "IDENTITY.md":
                continue
            user_scoped = name in ("USER.md",) or name in MEMORY_FAMILY
            for scoped in (user_scoped, False):
                key = self._key(name, user_scoped=scoped)
                try:
                    self.kv.set(key, b"")
                except Exception as e:  # storage failures must not break reset
                    log.warning("persona_reset_failed", doc=name, detail=str(e))

    # ── managed memory block ─────────────────────────────────────────────

    def read_managed(self) -> dict[str, list[str]]:
        """Parse the managed section of MEMORY.md into {section: [items]}."""
        memory = self.read("MEMORY.md")
        result: dict[str, list[str]] = {}
        in_managed = False
        current: str | None = None
        for line in memory.splitlines():
            if line.strip().startswith(MANAGED_HEADER):
                in_managed = True
                continue
            if not in_managed:
                continue
            if line.startswith("### "):
                current = line[4:].strip()
                result.setdefault(current, [])
            elif line.startswith("- ") and current:
                result[current].append(line[2:].strip())
        return result

    def free_text(self) -> str:
        """The un-managed (free-form) part of MEMORY.md, above the header."""
        memory = self.read("MEMORY.md")
        header_idx = memory.find(MANAGED_HEADER)
        return memory[:header_idx].rstrip() if header_idx >= 0 else memory.rstrip()

    def _write_managed(self, sections: dict[str, list[str]]) -> None:
        sections = {s: items for s, items in sections.items() if items}
        free_text = self.free_text()
        parts = [free_text] if free_text else []
        if sections:
            parts.append(MANAGED_HEADER)
            for section in sections:  # preserve document order (stable numbering)
                parts.append(f"### {section}")
                parts.extend(f"- {it}" for it in sections[section])
        self.write("MEMORY.md", "\n\n".join(parts).rstrip() + "\n")

    def merge_managed(self, updates: dict[str, list[str]], *, max_items: int = 12) -> None:
        """Merge extracted memory items into the managed block, deduped."""
        import re

        existing = self.read_managed()
        order = list(existing) + [s for s in updates if s not in existing]
        merged: dict[str, list[str]] = {}
        for section in order:
            items: list[str] = []
            seen: set[str] = set()
            for it in existing.get(section, []) + updates.get(section, []):
                key = re.sub(r"\s+", " ", it).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    items.append(it.strip())
            merged[section] = items[:max_items]
        self._write_managed(merged)

    # ── entry-level management (查看/删除/修改记忆 commands) ─────────────

    def managed_entries(self) -> list[tuple[str, str]]:
        """Flat (section, item) pairs in document order — stable numbering."""
        return [(sec, item) for sec, items in self.read_managed().items() for item in items]

    def delete_managed_entry(self, index: int) -> tuple[str, str] | None:
        """Remove flat entry #index (0-based); returns (section, item) or None."""
        sections = self.read_managed()
        flat = self.managed_entries()
        if not 0 <= index < len(flat):
            return None
        sec, item = flat[index]
        sections[sec].remove(item)
        self._write_managed(sections)
        return sec, item

    def edit_managed_entry(self, index: int, new_text: str) -> tuple[str, str, str] | None:
        """Replace flat entry #index; returns (section, old, new) or None."""
        sections = self.read_managed()
        flat = self.managed_entries()
        if not 0 <= index < len(flat):
            return None
        sec, old = flat[index]
        sections[sec][sections[sec].index(old)] = new_text.strip()
        self._write_managed(sections)
        return sec, old, new_text.strip()

    # ── prompt context ───────────────────────────────────────────────────

    def build_context(self, *, max_chars: int = 4000) -> str:
        identity = self.read("IDENTITY.md").strip()
        user = self.read("USER.md").strip()
        soul = self.read("SOUL.md").strip()
        memory = self.read("MEMORY.md").strip()
        blocks: list[str] = []
        for label, text in (("身份设定", identity), ("用户画像", user), ("风格规则", soul), ("长期记忆", memory)):
            if text:
                blocks.append(f"[{label}]\n{text}")
        out = "\n\n".join(blocks)
        return out[:max_chars]
