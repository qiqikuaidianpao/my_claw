"""Workspace model — explicit artifact lifecycle for one session.

Unifies mini_claw's file rules (uploads/, skill_outputs/, temp session dirs,
export marking, fingerprint dedup) into one place.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from core.errors import WorkspaceError
from core.session import SessionContext
from core.util import list_dir, safe_join


class Workspace:
    """Filesystem workspace bound to a session directory."""

    def __init__(self, root: str, *, keep_recent_sessions: int = 4, base_temp_dir: str = "") -> None:
        self.root = os.path.abspath(root)
        self.base_temp_dir = os.path.abspath(base_temp_dir) if base_temp_dir else os.path.dirname(self.root)
        self.keep_recent_sessions = keep_recent_sessions
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(os.path.join(self.root, "uploads"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "skill_outputs"), exist_ok=True)

    # ── basic ops ────────────────────────────────────────────────────────

    def resolve(self, relative_path: str) -> str:
        try:
            return safe_join(self.root, relative_path)
        except ValueError as e:
            raise WorkspaceError(f"path escape denied: {relative_path}") from e

    def write(self, relative_path: str, content: str, *, append: bool = False) -> str:
        path = self.resolve(relative_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        mode = "a" if append else "w"
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
        return path

    def read(self, relative_path: str, max_chars: int = 12000) -> str:
        path = self.resolve(relative_path)
        if not os.path.isfile(path):
            raise WorkspaceError(f"file not found: {relative_path}")
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)

    def inventory(self, max_depth: int = 4) -> list[dict[str, Any]]:
        return list_dir(self.root, max_depth=max_depth)

    def mark_artifact(self, ctx: SessionContext, relative_path: str, *, filename: str | None = None) -> None:
        rel = relative_path.replace("\\", "/").lstrip("/")
        ctx.register_artifact(rel, filename=filename)

    def read_artifacts(self, ctx: SessionContext) -> list[tuple[str, bytes, str, str]]:
        """Materialize registered artifacts as (rel, bytes, mime, filename)."""
        import hashlib

        out: list[tuple[str, bytes, str, str]] = []
        seen_fp: set[str] = set()
        for rel, art in ctx.artifacts.items():
            path = self.resolve(rel)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                data = f.read()
            fp = hashlib.sha1(data).hexdigest()
            key = f"{art.filename}|{art.mime_type}|{fp}"
            if key in seen_fp:
                continue
            seen_fp.add(key)
            out.append((rel, data, art.mime_type, art.filename))
        return out

    # ── lifecycle ────────────────────────────────────────────────────────

    def cleanup_old_sessions(self) -> int:
        """Rotate sibling session dirs, keeping the most recent N."""
        parent = self.base_temp_dir
        prefix = "myclaw-session-"
        if not os.path.isdir(parent):
            return 0
        candidates = [
            os.path.join(parent, name)
            for name in os.listdir(parent)
            if name.startswith(prefix) and os.path.isdir(os.path.join(parent, name))
        ]
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        removed = 0
        for stale in candidates[self.keep_recent_sessions :]:
            try:
                shutil.rmtree(stale, ignore_errors=True)
                removed += 1
            except Exception:
                pass
        return removed
