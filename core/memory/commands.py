"""Deterministic memory management commands — 查看/删除/修改记忆 + EN aliases.

Routed in the pre-flight chain so management never costs an LLM call.
Parsing is strict-anchored: the query must contain 「记忆」 plus an explicit
management verb (or the English verb + "memory"); capability questions like
「你记得我生日吗」 or "do you remember my birthday" fall through to the agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(\d+|[一二三四五六七八九十]{1,3})"
_PREFIX = r"^(?:帮我|请|麻烦|把)?\s*"

_LIST_RE = re.compile(
    _PREFIX + r"(?:查看|看看|看下|看|列出|列一下|显示)(?:一下)?记忆$|^记忆(?:列表|清单|一览)$"
)
_ARCHIVE_RE = re.compile(_PREFIX + r"(?:查看|看看|看|列出)(?:一下)?归档记忆$|^归档记忆(?:列表|清单)?$")
_DEL_RE = re.compile(
    _PREFIX + r"(?:删除|删掉|忘掉|忘记|清除|移除)\s*(?:第\s*" + _NUM + r"\s*(?:条|个)?\s*记忆|记忆\s*" + _NUM + r")$"
)
_EDIT_RE = re.compile(
    _PREFIX
    + r"(?:(?:修改|更新|改)\s*(?:第\s*(?P<n1>\d+|[一二三四五六七八九十]{1,3})\s*(?:条|个)?\s*记忆|记忆\s*(?P<n2>\d+|[一二三四五六七八九十]{1,3}))|记忆\s*(?P<n3>\d+|[一二三四五六七八九十]{1,3}))"
    + r"\s*(?:为|改成|改为|更改为|修改成)\s*(?P<new>.+)$"
)

# English aliases (case-insensitive; "memory" or "memories", optional "my"/"entry no.")
_EN_PREFIX = r"^(?:please\s+)?(?:help\s+me\s+)?"
_EN_LIST_RE = re.compile(_EN_PREFIX + r"(?:view|show|list|display)\s+(?:my\s+)?memor(?:y|ies)$|^memory list$", re.I)
_EN_ARCHIVE_RE = re.compile(_EN_PREFIX + r"(?:view|show|list)\s+(?:my\s+)?archived?\s+memor(?:y|ies)$|^memory archive$", re.I)
_EN_DEL_RE = re.compile(_EN_PREFIX + r"(?:delete|remove|forget)\s+(?:my\s+)?memor(?:y|ies)\s*(?:entry\s*)?(?:no\.?\s*)?(\d+)$", re.I)
_EN_EDIT_RE = re.compile(
    _EN_PREFIX + r"(?:edit|update|change)\s+(?:my\s+)?memor(?:y|ies)\s*(?:entry\s*)?(?:no\.?\s*)?(\d+)\s+to\s+(.+)$", re.I
)


@dataclass
class MemoryCommand:
    action: str  # "list" | "archive" | "delete" | "edit"
    index: int = 0  # 0-based flat entry index
    new_text: str = ""
    lang: str = "zh"  # reply language follows the command language


def _cn_to_int(token: str) -> int | None:
    """Parse arabic or simple Chinese numerals (一…二十)."""
    if token.isdigit():
        return int(token)
    total = 0
    for ch in token:
        if ch == "十":
            total = (total or 1) * 10
        elif ch in _CN_NUM:
            total += _CN_NUM[ch]
        else:
            return None
    return total or None


def parse_memory_command(query: str) -> MemoryCommand | None:
    s = query.strip()
    if len(s) > 80:
        return None
    if "记忆" in s:
        m = _EDIT_RE.match(s)
        if m:
            n = _cn_to_int(m.group("n1") or m.group("n2") or m.group("n3") or "")
            new = (m.group("new") or "").strip()
            if n and n >= 1 and new:
                return MemoryCommand("edit", n - 1, new)
        m = _DEL_RE.match(s)
        if m:
            n = _cn_to_int(m.group(1) or m.group(2) or "")
            if n and n >= 1:
                return MemoryCommand("delete", n - 1)
        if _ARCHIVE_RE.match(s):
            return MemoryCommand("archive")
        if _LIST_RE.match(s):
            return MemoryCommand("list")
        return None
    if re.search(r"memor(?:y|ies)", s, re.I):
        m = _EN_EDIT_RE.match(s)
        if m:
            n = int(m.group(1))
            new = (m.group(2) or "").strip()
            if n >= 1 and new:
                return MemoryCommand("edit", n - 1, new, lang="en")
        m = _EN_DEL_RE.match(s)
        if m:
            n = int(m.group(1))
            if n >= 1:
                return MemoryCommand("delete", n - 1, lang="en")
        if _EN_ARCHIVE_RE.match(s):
            return MemoryCommand("archive", lang="en")
        if _EN_LIST_RE.match(s):
            return MemoryCommand("list", lang="en")
    return None


def format_memory_list(persona, lang: str = "zh") -> str:
    free = persona.free_text().strip()
    entries = persona.managed_entries()
    if lang == "en":
        lines = ["📚 Long-term memory"]
        if free:
            lines += ["", free]
        if entries:
            lines += ["", f"({len(entries)} entries)"]
            lines.extend(f"{i}. {item}" for i, (_, item) in enumerate(entries, 1))
        else:
            lines += ["", "No memory entries yet — notable details get archived here automatically as you chat."]
        archive = [l[2:].strip() for l in persona.read("MEMORY.archive.md").splitlines() if l.startswith("- ")]
        if archive:
            lines += ["", f"📦 {len(archive)} archived experiences older than 60 days — say \"view archived memory\" to browse."]
        lines += ["", "💡 Manage: delete memory N / edit memory N to … (N = the number above)"]
        return "\n".join(lines)
    lines = ["📚 长期记忆"]
    if free:
        lines += ["", free]
    if entries:
        lines += ["", f"（共 {len(entries)} 条）"]
        lines.extend(f"{i}. {item}" for i, (_, item) in enumerate(entries, 1))
    else:
        lines += ["", "还没有沉淀的记忆条目——值得记住的内容会在对话中自动归档。"]
    archive = [l[2:].strip() for l in persona.read("MEMORY.archive.md").splitlines() if l.startswith("- ")]
    if archive:
        lines += ["", f"📦 另有 {len(archive)} 条已归档的久远经历（「查看归档记忆」可翻看）"]
    lines += ["", "💡 管理命令：删除记忆N / 修改记忆N为…（N 为上面的编号）"]
    return "\n".join(lines)


def format_archive_list(persona, lang: str = "zh") -> str:
    raw = persona.read("MEMORY.archive.md").strip()
    items = [l[2:].strip() for l in raw.splitlines() if l.startswith("- ")]
    if lang == "en":
        if not items:
            return "📦 Memory archive is empty — experiences untouched for 60+ days get moved here (kept, never deleted)."
        lines = ["📦 Archived memories (experiences untouched for 60+ days)"]
        lines.extend(f"{i}. {it}" for i, it in enumerate(items, 1))
        return "\n".join(lines)
    if not items:
        return "📦 归档记忆是空的——超过60天未更新的「经历」类条目整理时会移到这里（不删除，只归档）。"
    lines = ["📦 归档记忆（超60天未更新的经历）"]
    lines.extend(f"{i}. {it}" for i, it in enumerate(items, 1))
    return "\n".join(lines)


def _out_of_range(n: int, total: int, lang: str) -> str:
    if lang == "en":
        if total:
            return f"⚠️ There is no entry {n} (only {total} entries). Say \"view memory\" to list them."
        return f"⚠️ No memory entries yet, so there is no entry {n} to touch."
    if total:
        return f"⚠️ 没有第 {n} 条记忆（当前共 {total} 条）。回复「查看记忆」可看全部编号。"
    return f"⚠️ 还没有任何记忆条目，没有第 {n} 条可操作。"


def execute_memory_command(persona, cmd: MemoryCommand) -> str:
    if cmd.action == "list":
        return format_memory_list(persona, cmd.lang)
    if cmd.action == "archive":
        return format_archive_list(persona, cmd.lang)

    total = len(persona.managed_entries())
    if cmd.action == "delete":
        removed = persona.delete_managed_entry(cmd.index)
        if removed is None:
            return _out_of_range(cmd.index + 1, total, cmd.lang)
        _, item = removed
        if cmd.lang == "en":
            return f"🗑️ Deleted entry {cmd.index + 1}: {item}\n({total - 1} left — \"view memory\" lists them all)"
        return f"🗑️ 已删除第 {cmd.index + 1} 条：{item}\n（现存 {total - 1} 条，「查看记忆」可看全部）"

    if cmd.action == "edit":
        updated = persona.edit_managed_entry(cmd.index, cmd.new_text)
        if updated is None:
            return _out_of_range(cmd.index + 1, total, cmd.lang)
        _, old, new = updated
        if cmd.lang == "en":
            return f"✏️ Updated entry {cmd.index + 1}:\nold: {old}\nnew: {new}"
        return f"✏️ 已更新第 {cmd.index + 1} 条记忆：\n旧：{old}\n新：{new}"

    return "未知记忆命令" if cmd.lang == "zh" else "Unknown memory command"
