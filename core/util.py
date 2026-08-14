"""Pure utility helpers shared across the core kernel.

Platform-agnostic by design: no dify_plugin / SDK imports allowed in this
package so the kernel stays independently unit-testable.
"""
from __future__ import annotations

import json
import mimetypes
import os
from typing import Any

MAX_SKILLDOC_CHARS = 12000


def safe_get(obj: Any, key: str) -> Any:
    """Dict/attr/index tolerant getter for SDK objects of uncertain shape."""
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except Exception:
        pass
    try:
        return getattr(obj, key)
    except Exception:
        return None


def shorten_text(value: Any, max_len: int = 500) -> str:
    """Compact single-line representation for logs and error details."""
    try:
        s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        s = str(value)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


_MIME_OVERRIDES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def guess_mime_type(filename: str) -> str:
    name = (filename or "").strip().lower()
    _, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    mime_type, _ = mimetypes.guess_type(name, strict=False)
    return mime_type or "application/octet-stream"


def safe_join(root: str, relative_path: str) -> str:
    """Join and verify the result stays inside root (path-escape guard)."""
    root_abs = os.path.abspath(root)
    joined = os.path.abspath(os.path.join(root_abs, relative_path))
    if os.path.commonpath([root_abs, joined]) != root_abs:
        raise ValueError("path is outside root")
    return joined


def read_text(path: str, max_chars: int = MAX_SKILLDOC_CHARS) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(max_chars)


def list_dir(root: str, max_depth: int = 2) -> list[dict[str, Any]]:
    """Shallow directory inventory with relative paths."""
    root_abs = os.path.abspath(root)
    entries: list[dict[str, Any]] = []
    root_depth = root_abs.count(os.sep)
    for current_root, dirs, files in os.walk(root_abs):
        depth = current_root.count(os.sep) - root_depth
        if depth > max_depth:
            dirs[:] = []
            continue
        for name in sorted(dirs):
            rel = os.path.relpath(os.path.join(current_root, name), root_abs)
            entries.append({"type": "dir", "path": os.path.join(current_root, name), "relative_path": rel})
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(current_root, name), root_abs)
            entries.append({"type": "file", "path": os.path.join(current_root, name), "relative_path": rel})
    return entries


def split_message_content(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Split multimodal message content into (text, nontext parts)."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if isinstance(content, (list, tuple)):
        text_parts: list[str] = []
        nontext_parts: list[dict[str, Any]] = []
        for item in content:
            item_dict = _coerce_content_item(item)
            if not item_dict:
                continue
            if item_dict.get("type") == "text":
                data = item_dict.get("data", item_dict.get("text"))
                if isinstance(data, str) and data:
                    text_parts.append(data)
            else:
                nontext_parts.append(item_dict)
        return "".join(text_parts), nontext_parts
    return "", [{"type": "unknown", "value": str(content)}]


def _coerce_content_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        return item
    try:
        import json as _json

        parsed = _json.loads(item)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    if hasattr(item, "model_dump"):
        try:
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    return None
