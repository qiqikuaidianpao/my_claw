"""Sleep-time memory consolidation — daily extraction and the "dream" pass.

Once per day (first task after midnight) the previous day's digest is mined
for [事实]/[偏好]/[经历] entries; when MEMORY.md grows past the entry
threshold a consolidation pass merges duplicates, keeps the newer side of
conflicts, and archives stale [经历] items to MEMORY.archive.md. The
original is backed up to MEMORY.bak.md (one generation) only after the LLM
output parses — a parse failure leaves everything untouched.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from core import log

ARCHIVE_DAYS = 60
CONSOLIDATE_THRESHOLD = 30
CONSOLIDATE_CAP = 40

_EXTRACT_SYSTEM = (
    "你是记忆提取器。从对话记录中提取关于用户的长期记忆。只输出JSON对象："
    '{"user_preferences": [...], "project_facts": [...], "episodic": [...]}\n'
    "- user_preferences 每条以「[偏好] 」开头\n"
    "- project_facts 每条以「[事实] 」开头\n"
    "- episodic 每条以「[经历] 」开头，并在行尾加「（来自M月d日对话）」，日期取记录里的实际日期\n"
    "每条不超60字。没有值得长期记住的就输出 {}。"
)

_CONSOLIDATE_SYSTEM = (
    "你是记忆整理器。输入是编号的记忆条目（每行：N. [类型] 内容）。请：\n"
    "1. 合并语义重复的条目，保留信息最全的一条\n"
    "2. 有矛盾时保留较新的——行尾（来自M月d日对话）日期越晚越新，无日期视为较旧\n"
    "3. 删除空洞或已无价值的条目\n"
    "只输出JSON字符串数组，元素为整理后的条目原文（保留[类型]前缀），尽量维持原顺序。"
)

_DATE_RE = re.compile(r"来自(\d{1,2})月(\d{1,2})日对话")
_EXTRACT_MARKER = "claw:memory:extracted"


def _parse_json_payload(raw: str):
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except ValueError:
            return None
    start, end = raw.find("["), raw.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except ValueError:
            return None
    return None


def _entry_date(item: str) -> datetime | None:
    """Parse the （来自M月d日对话） suffix into a datetime (this or last year)."""
    m = _DATE_RE.search(item)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    today = datetime.now()
    try:
        d = datetime(today.year, month, day)
    except ValueError:
        return None
    if d > today:
        d = datetime(today.year - 1, month, day)
    return d


def _stale_episodic(item: str, *, days: int = ARCHIVE_DAYS) -> bool:
    d = _entry_date(item)
    return d is not None and d < datetime.now() - timedelta(days=days)


# ── daily extraction ───────────────────────────────────────────────────────

def daily_extract(memory, persona, llm) -> bool:
    """Extract yesterday's completed digest into MEMORY.md. Once per day.

    Returns True when this call performed the extraction (so the caller may
    chain a consolidation pass right after).
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    scope = f":user:{memory.user_id}" if memory.user_id else ""
    marker_key = f"{_EXTRACT_MARKER}:{memory.app_id}{scope}:{yesterday}"
    if memory.kv.get(marker_key):
        return False
    raw = memory.kv.get(memory._day_key(yesterday))
    digest = raw.decode("utf-8", errors="ignore").strip() if raw else ""
    memory.kv.set(marker_key, b"1")
    if not digest or llm is None:
        return False
    try:
        out = llm.invoke_text(system=_EXTRACT_SYSTEM, messages=[{"role": "user", "content": digest[:6000]}])
    except Exception as e:
        log.warning("daily_extract_llm_failed", day=yesterday, detail=str(e))
        return False
    data = _parse_json_payload(out or "")
    if not isinstance(data, dict):
        log.warning("daily_extract_parse_failed", day=yesterday)
        return False
    updates: dict[str, list[str]] = {}
    for section in ("user_preferences", "project_facts", "episodic"):
        items = data.get(section)
        if isinstance(items, list) and items:
            updates[section] = [str(x).strip() for x in items if str(x).strip()][:8]
    if updates:
        try:
            persona.merge_managed(updates)
            log.info("daily_extract_merged", day=yesterday, sections=list(updates))
        except Exception as e:
            log.warning("daily_extract_merge_failed", day=yesterday, detail=str(e))
    return True


# ── consolidation ("dream") ────────────────────────────────────────────────

def should_consolidate(persona, *, threshold: int = CONSOLIDATE_THRESHOLD) -> bool:
    return len(persona.managed_entries()) > threshold


def consolidate(persona, llm, *, threshold: int = CONSOLIDATE_THRESHOLD, cap: int = CONSOLIDATE_CAP) -> str:
    """Merge/resolve/archive when MEMORY.md exceeds the entry threshold.

    Order matters for safety: the LLM rewrite is parsed first; only then is
    the original saved to MEMORY.bak.md and the new content committed. A
    parse failure aborts with MEMORY.md untouched.
    """
    entries = persona.managed_entries()
    if len(entries) <= threshold or llm is None:
        return ""
    lines = [f"{i}. {item}" for i, (_, item) in enumerate(entries, 1)]
    try:
        out = llm.invoke_text(system=_CONSOLIDATE_SYSTEM, messages=[{"role": "user", "content": "\n".join(lines)}])
    except Exception as e:
        log.warning("consolidate_llm_failed", detail=str(e))
        return "❌ 记忆整理调用失败，本次跳过（记忆未改动）"
    kept = _parse_json_payload(out or "")
    if not isinstance(kept, list) or not kept:
        log.warning("consolidate_parse_failed")
        return "❌ 记忆整理结果解析失败，本次放弃（记忆未改动）"
    cleaned = [str(x).strip() for x in kept if str(x).strip()][:cap]

    # deterministic archive: stale [经历] leave MEMORY.md but stay browsable
    archived = [it for it in cleaned if it.startswith("[经历]") and _stale_episodic(it)]
    cleaned = [it for it in cleaned if it not in archived]

    original = persona.read("MEMORY.md")
    # rebuild sections from the consolidated flat list, preserving section names
    sections: dict[str, list[str]] = {}
    sec_by_item = {item: sec for sec, item in entries}
    for it in cleaned:
        sec = sec_by_item.get(it, "user_facts")
        sections.setdefault(sec, []).append(it)
    persona.write("MEMORY.bak.md", original)
    persona._write_managed(sections)
    if archived:
        prev = persona.read("MEMORY.archive.md").strip()
        day = datetime.now().strftime("%Y-%m-%d")
        block = f"\n## 归档于 {day}\n" + "\n".join(f"- {it}" for it in archived)
        persona.write("MEMORY.archive.md", (prev + block if prev else "# 归档记忆\n" + block) + "\n")
    log.info("memory_consolidated", before=len(entries), after=len(cleaned), archived=len(archived))
    return f"🧹 记忆整理完成：{len(entries)} 条 → {len(cleaned)} 条（归档 {len(archived)} 条，原记忆已备份到 MEMORY.bak.md）"


def maybe_consolidate(persona, llm) -> str:
    if should_consolidate(persona):
        return consolidate(persona, llm)
    return ""
