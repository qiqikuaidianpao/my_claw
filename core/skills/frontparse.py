"""Stdlib-only front-matter parser for SKILL.md manifests.

Plugin runtimes must not rely on PyYAML being installable on the host —
this is the same constraint that led mini_claw to hand-parse front matter.
We accept the subset that skill manifests actually use:

* flat ``key: value`` scalars (quoted or bare, # comments stripped outside quotes)
* nested mappings by indentation
* inline lists ``[a, b, c]``
* block lists ``- item`` (scalars)
* literal blocks ``|`` / ``>`` (kept as text / folded to spaces)

Anything outside the subset raises FrontmatterError instead of guessing —
a pack either parses deterministically or is reported invalid.
"""
from __future__ import annotations

from typing import Any


class FrontmatterError(ValueError):
    pass


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, body)."""
    text = content.lstrip("\ufeff").replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_lines = lines[1:i]
            body = "\n".join(lines[i + 1:])
            return _parse_block(fm_lines, 0)[0] or {}, body
    return {}, text


def _strip_comment(line: str) -> str:
    out: list[str] = []
    in_s: str | None = None
    for ch in line:
        if in_s:
            out.append(ch)
            if ch == in_s:
                in_s = None
        elif ch in ('"', "'"):
            in_s = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in _split_inline(inner)]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return ""
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_inline(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_s: str | None = None
    for ch in inner:
        if in_s:
            buf.append(ch)
            if ch == in_s:
                in_s = None
        elif ch in ('"', "'"):
            in_s = ch
            buf.append(ch)
        elif ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _parse_block(lines: list[str], start: int) -> tuple[dict[str, Any] | None, int]:
    """Parse an indented mapping starting at lines[start]; returns (dict, next_index)."""
    result: dict[str, Any] = {}
    i = start
    parent_indent: int | None = None
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        if parent_indent is None:
            parent_indent = ind
        elif ind < parent_indent:
            break
        elif ind > parent_indent:
            raise FrontmatterError(f"unexpected indent at line {i + 1}: {line.strip()[:40]}")
        stripped = _strip_comment(line)
        if not stripped.strip():
            i += 1
            continue
        if stripped.strip().startswith("- "):
            raise FrontmatterError(f"list item outside list context at line {i + 1}")
        if ":" not in stripped:
            raise FrontmatterError(f"expected 'key: value' at line {i + 1}: {stripped.strip()[:40]}")
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest in ("|", ">"):
            block: list[str] = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > ind):
                block.append(lines[i])
                i += 1
            joined = "\n".join(block).rstrip()
            result[key] = joined if rest == "|" else " ".join(joined.split())
            continue
        if rest == "":
            # nested block (mapping or list) or empty value
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > ind):
                j += 1
            child = [lines[k] for k in range(i + 1, j)]
            first = next((l for l in child if l.strip()), None)
            if first and first.strip().startswith("- "):
                result[key] = _parse_list(child, ind)
            elif child:
                sub, _ = _parse_block(child, 0)
                result[key] = sub or {}
            else:
                result[key] = ""
            i = j
            continue
        result[key] = _parse_scalar(rest)
        i += 1
    return result, i


def _parse_list(lines: list[str], parent_indent: int) -> list[Any]:
    items: list[Any] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        s = _strip_comment(line).strip()
        if s.startswith("- "):
            # collect any indented continuation belonging to this item
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > ind):
                j += 1
            child = [lines[k] for k in range(i + 1, j)]
            body = s[2:].strip()
            if ":" in body and not body.startswith(("[", '"', "'")):
                # mapping item: treat "- key: value" plus child lines as a block
                sub_lines = [" " * (ind + 2) + body] + child
                sub, _ = _parse_block(sub_lines, 0)
                items.append(sub or {})
            elif child:
                raise FrontmatterError(f"unsupported list item continuation: {body[:30]}")
            else:
                items.append(_parse_scalar(body))
            i = j
        else:
            raise FrontmatterError(f"unexpected line in list: {s[:30]}")
    return items
