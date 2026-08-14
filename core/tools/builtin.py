"""Built-in agent tools, registered declaratively (no if/elif dispatch).

v0.1 surface: workspace file ops, controlled command execution, skill pack
reading (install/management lives in the separate skill manager tool).
Each handler receives (ctx, emitter, **args) and returns a ToolResult whose
content is the JSON observation the model sees.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from core.security import exec_policy
from core.session import SessionContext
from core.tools.registry import ToolResult, tool
from core.util import read_text, safe_join, shorten_text
from core.workspace.workspace import Workspace

_MAX_CMD_OUTPUT = 20000
_CMD_TIMEOUT = 120


def _ws(ctx: SessionContext) -> Workspace:
    return Workspace(ctx.workspace_root or os.getcwd())


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, default=str))


# ── workspace files ─────────────────────────────────────────────────────


@tool(
    "list_workspace_files",
    description="List files and subdirectories in the current session workspace.",
    parameters={"type": "object", "properties": {"max_depth": {"type": "integer", "default": 4}}},
)
def list_workspace_files(ctx: SessionContext, emitter, max_depth: int = 4) -> ToolResult:
    entries = _ws(ctx).inventory(max_depth=max_depth)
    return _ok({"files": [{"type": e["type"], "path": e["relative_path"]} for e in entries[:500]]})


@tool(
    "read_file",
    description="Read a text file from the session workspace (or a skill pack, using skill:NAME/ prefix).",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}},
    },
    required=("path",),
)
def read_file(ctx: SessionContext, emitter, path: str, max_chars: int = 12000) -> ToolResult:
    if path.startswith("skill:") and ctx.skills_root:
        real = safe_join(ctx.skills_root, path[len("skill:") :])
        if not os.path.isfile(real):
            return _ok({"error": "not_found", "path": path})
        return _ok({"path": path, "content": read_text(real, max_chars)})
    try:
        content = _ws(ctx).read(path, max_chars)
    except Exception as e:
        return _ok({"error": "read_failed", "detail": str(e)})
    return _ok({"path": path, "content": content})


@tool(
    "write_file",
    description="Write (or append to) a UTF-8 text file in the session workspace.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean", "default": False}},
    },
    required=("path", "content"),
    progress="✍️ 正在写入文件：{path}…",
)
def write_file(ctx: SessionContext, emitter, path: str, content: str, append: bool = False) -> ToolResult:
    try:
        real = _ws(ctx).write(path, content, append=append)
    except Exception as e:
        return _ok({"error": "write_failed", "detail": str(e)})
    return _ok({"path": path, "written": len(content), "abs_path": real})


@tool(
    "export_file",
    description="Mark a workspace file as a user-facing deliverable (it will be attached to the reply).",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ("path",)},
    progress="📦 正在标记交付文件：{path}…",
)
def export_file(ctx: SessionContext, emitter, path: str) -> ToolResult:
    real = _ws(ctx).resolve(path)
    if not os.path.isfile(real):
        return _ok({"error": "not_found", "path": path})
    _ws(ctx).mark_artifact(ctx, path)
    return _ok({"path": path, "exported": True})


# ── command execution ───────────────────────────────────────────────────


@tool(
    "run_command",
    description="Run an allow-listed command in the session workspace (e.g. python3 script.py). Shell wrappers and absolute executables are denied.",
    parameters={
        "type": "object",
        "properties": {"command": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer", "default": 120}},
    },
    required=("command",),
    progress="⚙️ 正在执行命令…",
)
def run_command(ctx: SessionContext, emitter, command: list[str], timeout: int = _CMD_TIMEOUT) -> ToolResult:
    if not isinstance(command, list) or not command:
        return _ok({"error": "invalid_command", "detail": "command must be a non-empty argv array"})
    verdict = exec_policy.resolve_and_validate_exec(
        command=[str(c) for c in command],
        session_dir=ctx.workspace_root or os.getcwd(),
        skills_root=ctx.skills_root or None,
    )
    if not verdict.get("ok"):
        return _ok({"error": verdict.get("error", "exec_denied"), "detail": verdict.get("detail") or verdict.get("error", ""), "hint": verdict.get("hint", "")})
    try:
        proc = subprocess.run(
            verdict["argv"],
            cwd=ctx.workspace_root or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout), _CMD_TIMEOUT)),
        )
    except subprocess.TimeoutExpired:
        return _ok({"error": "timeout", "timeout": timeout})
    except Exception as e:
        return _ok({"error": "exec_failed", "detail": str(e)})
    return _ok(
        {
            "code": proc.returncode,
            "stdout": shorten_text(proc.stdout, _MAX_CMD_OUTPUT),
            "stderr": shorten_text(proc.stderr, 4000) if proc.stderr.strip() else "",
        }
    )


# ── skills (read-only; management in the skill manager tool) ────────────


@tool(
    "list_skills",
    description="List installed skill packs with their eligibility status.",
    parameters={"type": "object", "properties": {}},
)
def list_skills(ctx: SessionContext, emitter) -> ToolResult:
    from core.skills.packages import list_installed

    return _ok({"skills": list_installed(ctx.skills_root)})


@tool(
    "read_skill_file",
    description="Read a file from an installed skill pack (progressive disclosure). Start with SKILL.md.",
    parameters={
        "type": "object",
        "properties": {"skill_name": {"type": "string"}, "file": {"type": "string", "default": "SKILL.md"}, "max_chars": {"type": "integer", "default": 12000}},
    },
    required=("skill_name",),
    progress="📖 正在读取技能《{skill_name}》文件：{file}…",
)
def read_skill_file(ctx: SessionContext, emitter, skill_name: str, file: str = "SKILL.md", max_chars: int = 12000) -> ToolResult:
    if not ctx.skills_root:
        return _ok({"error": "no_skills_root"})
    try:
        real = safe_join(safe_join(ctx.skills_root, skill_name), file)
    except ValueError:
        return _ok({"error": "path_denied"})
    if not os.path.isfile(real):
        return _ok({"error": "not_found", "skill": skill_name, "file": file})
    return _ok({"skill": skill_name, "file": file, "content": read_text(real, max_chars)})
