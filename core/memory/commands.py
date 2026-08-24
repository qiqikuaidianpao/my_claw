"""Deterministic memory management commands — 查看/删除/修改记忆.

Routed in the pre-flight chain so management never costs an LLM call.
Parsing is strict-anchored: the query must contain 「记忆」 plus an explicit
management verb; capability questions like 「你记得我生日吗」 or 「怎么删除记忆」
fall through to the normal agent loop.
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
_DEL_RE = re.compile(
    _PREFIX + r"(?:删除|删掉|忘掉|忘记|清除|移除)\s*(?:第\s*" + _NUM + r"\s*(?:条|个)?\s*记忆|记忆\s*" + _NUM + r")$"
)
_EDIT_RE = re.compile(
    _PREFIX
    + r"(?:(?:修改|更新|改)\s*(?:第\s*(?P<n1>\d+|[一二三四五六七八九十]{1,3})\s*(?:条|个)?\s*记忆|记忆\s*(?P<n2>\d+|[一二三四五六七八九十]{1,3}))|记忆\s*(?P<n3>\d+|[一二三四五六七八九十]{1,3}))"
    + r"\s*(?:为|改成|改为|更改为|修改成)\s*(?P<new>.+)$"
)


@dataclass
class MemoryCommand:
    action: str  # "list" | "delete" | "edit"
    index: int = 0  # 0-based flat entry index
    new_text: str = ""


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
    if "记忆" not in s or len(s) > 60:
        return None
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
    if _LIST_RE.match(s):
        return MemoryCommand("list")
    return None


def format_memory_list(persona) -> str:
    free = persona.free_text().strip()
    entries = persona.managed_entries()
    lines = ["📚 长期记忆"]
    if free:
        lines += ["", free]
    if entries:
        lines += ["", f"（共 {len(entries)} 条）"]
        lines.extend(f"{i}. {item}" for i, (_, item) in enumerate(entries, 1))
    else:
        lines += ["", "还没有沉淀的记忆条目——值得记住的内容会在对话中自动归档。"]
    lines += ["", "💡 管理命令：删除记忆N / 修改记忆N为…（N 为上面的编号）"]
    return "\n".join(lines)


def _out_of_range(n: int, total: int) -> str:
    if total:
        return f"⚠️ 没有第 {n} 条记忆（当前共 {total} 条）。回复「查看记忆」可看全部编号。"
    return f"⚠️ 还没有任何记忆条目，没有第 {n} 条可操作。"


def execute_memory_command(persona, cmd: MemoryCommand) -> str:
    if cmd.action == "list":
        return format_memory_list(persona)

    total = len(persona.managed_entries())
    if cmd.action == "delete":
        removed = persona.delete_managed_entry(cmd.index)
        if removed is None:
            return _out_of_range(cmd.index + 1, total)
        _, item = removed
        return f"🗑️ 已删除第 {cmd.index + 1} 条：{item}\n（现存 {total - 1} 条，「查看记忆」可看全部）"

    if cmd.action == "edit":
        updated = persona.edit_managed_entry(cmd.index, cmd.new_text)
        if updated is None:
            return _out_of_range(cmd.index + 1, total)
        _, old, new = updated
        return f"✏️ 已更新第 {cmd.index + 1} 条记忆：\n旧：{old}\n新：{new}"

    return "未知记忆命令"
