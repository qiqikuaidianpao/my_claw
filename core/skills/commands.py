"""Deterministic routing for explicit skill-management commands."""
from __future__ import annotations

import re


_REQUEST_PREFIX_RE = re.compile(r"^(?:请你?|帮我|麻烦你?|劳驾|我要|我想|能否|可以|帮忙)\s*[，,:：]?\s*")


def parse_skill_management_command(raw: str, *, allow_enum: bool = False) -> tuple[str, int] | None:
    """Return a management action only when ``raw`` expresses explicit intent."""
    text = raw.strip()
    lowered = text.lower()
    if allow_enum and lowered in {"list", "install", "remove", "download", "dependencies"}:
        return lowered, 0

    body = text
    while True:
        stripped = _REQUEST_PREFIX_RE.sub("", body, count=1)
        if stripped == body:
            break
        body = stripped

    dependency_patterns = (
        r"^(?:安装|补全).{0,8}依赖",
        r"^依赖.{0,6}(?:安装|补全)",
    )
    if any(re.search(pattern, body) for pattern in dependency_patterns):
        return "dependencies", 0

    indexed_actions = (
        ("remove", r"删除|卸载|移除"),
        ("download", r"下载|导出"),
    )
    for action, verbs in indexed_actions:
        patterns = (
            rf"^(?:{verbs}).{{0,16}}技能",
            rf"^(?:把)?(?:第?\d+个?)?技能.{{0,16}}(?:{verbs})",
        )
        if any(re.search(pattern, body) for pattern in patterns):
            match = re.search(r"\d+", body)
            return action, int(match.group()) if match else 0

    install_verbs = r"新增|添加|安装|上传|导入|存入|保存"
    install_patterns = (
        rf"^(?:{install_verbs}).{{0,16}}技能",
        rf"^(?:把)?技能.{{0,16}}(?:{install_verbs})",
    )
    if any(re.search(pattern, body) for pattern in install_patterns):
        return "install", 0

    list_patterns = (
        r"^(?:查看|看看|看下|看一下|列出|显示|浏览|查询).{0,16}技能",
        r"^技能.{0,6}(?:列表|清单)",
    )
    if any(re.search(pattern, body) for pattern in list_patterns):
        return "list", 0
    return None
