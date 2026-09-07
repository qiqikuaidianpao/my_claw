"""Guard against models faking tool calls/results as plain text.

Only providers' structured tool_calls are executable. Some models instead
write "write_file(...)", fake tool returns, or "already exported" claims into
the reply text and present it as a delivered result. This guard detects that
shape so the kernel can retry or fail truthfully — it never parses or
executes anything from the text.
"""
from __future__ import annotations

import re

# Tools whose faked invocation would claim a side effect or a deliverable.
GUARDED_TOOLS = ("read_skill_file", "write_file", "run_command", "run_skill_command", "export_file")

# Adjacent CJK characters are \w in Python re, so \b never fires between
# Chinese prose and a tool name — use explicit ASCII boundaries instead.
_NOT_IDENT = r"(?![A-Za-z0-9_])"

_CALL_SYNTAX_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(t) for t in GUARDED_TOOLS) + r")\s*\("
)

_TOOL_NAME_RES = tuple(
    re.compile(r"(?<![A-Za-z0-9_])" + re.escape(t) + _NOT_IDENT) for t in GUARDED_TOOLS
)

# Markers a model uses to narrate a tool round it never performed.
_RESULT_MARKER_RES = (
    re.compile(r"工具返回"),
    re.compile(r"工具结果"),
    re.compile(r"模拟工具"),
    re.compile(r"tool\s*result", re.IGNORECASE),
    re.compile(r"tool\s*call", re.IGNORECASE),
)

# Code spans are how legitimate replies *show* tool usage for teaching; fake
# rounds narrate around them, so detection runs on the prose only.
_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code_spans(text: str) -> str:
    return _INLINE_CODE_RE.sub("", _FENCED_BLOCK_RE.sub("", text))


def looks_like_fake_tool_text(text: str, *, has_real_tool_calls: bool = False) -> bool:
    """True when plain text pretends to invoke tools or narrate tool results.

    Bare tool names in explanatory prose ("你可以使用 write_file 保存内容") and
    code examples inside fenced/inline code spans do not match. Result-marker
    narration is only treated as fake when the session has no real structured
    tool calls to report on — otherwise it is a truthful result summary.
    """
    if not text:
        return False
    prose = _strip_code_spans(text)
    if not prose:
        return False
    if _CALL_SYNTAX_RE.search(prose):
        return True
    if has_real_tool_calls:
        return False
    if any(m.search(prose) for m in _RESULT_MARKER_RES):
        return any(r.search(prose) for r in _TOOL_NAME_RES)
    return False
