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
import sys
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

SENSITIVE_BINS = {"curl", "wget", "pip", "pip3", "npm", "npx", "node", "git", "tar", "unzip", "ssh", "scp"}


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
    approval_check = (ctx.extra or {}).get("approval_check")
    raw_exe = str(command[0] or "").strip().lower()
    verdict = exec_policy.resolve_and_validate_exec(
        command=[str(c) for c in command],
        session_dir=ctx.workspace_root or os.getcwd(),
        skills_root=ctx.skills_root or None,
    )
    # 审批闸优先于白名单：开启审批时，敏感命令交用户裁决而非直接拒绝
    if approval_check is not None and raw_exe in SENSITIVE_BINS:
        decision = approval_check([str(c) for c in command], timeout)
        if decision == "pending":
            return _ok({"approval_required": True, "command": [str(c) for c in command]})
        if decision == "denied":
            return _ok({"error": "exec_denied_by_user"})
        if not verdict.get("ok"):
            # 用户已放行：以原始命令执行（白名单为其让路）
            verdict = {"ok": True, "argv": [str(c) for c in command]}
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
    "web_fetch",
    description="Fetch a public web page and return readable markdown/text (SSRF-guarded).",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 20000}},
    },
    required=("url",),
    progress="🌐 正在抓取网页：{url}…",
)
def web_fetch(ctx: SessionContext, emitter, url: str, max_chars: int = 20000) -> ToolResult:
    from core.security.web_fetch import web_fetch as _fetch

    result = _fetch(url=url, max_chars=max_chars)
    return _ok(result)


@tool(
    "update_persona",
    description="Persist durable facts about the user or yourself into long-term persona memory.",
    parameters={
        "type": "object",
        "properties": {
            "user_profile": {"type": "string", "description": "facts about the user, one per line"},
            "identity": {"type": "string", "description": "your own name/style updates"},
            "memory_items": {"type": "array", "items": {"type": "string"}, "description": "long-term memory bullets"},
        },
    },
)
def update_persona(ctx: SessionContext, emitter, user_profile: str = "", identity: str = "", memory_items: list[str] | None = None) -> ToolResult:
    persona = (ctx.extra or {}).get("persona")
    if persona is None:
        return _ok({"error": "persona_unavailable"})
    written: list[str] = []
    if user_profile:
        persona.write("USER.md", (persona.read("USER.md").rstrip() + "\n" + user_profile.strip()).strip())
        written.append("USER.md")
    if identity:
        persona.write("IDENTITY.md", (persona.read("IDENTITY.md").rstrip() + "\n" + identity.strip()).strip())
        written.append("IDENTITY.md")
    if memory_items:
        persona.merge_managed({"facts": [str(x) for x in memory_items]})
        written.append("MEMORY.md")
    return _ok({"updated": written})


@tool(
    "run_skill_command",
    description="Run a skill's script inside its own directory (interpreter-resolved for python) and collect produced files.",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string"},
            "command": {"type": "array", "items": {"type": "string"}},
            "timeout": {"type": "integer", "default": 120},
        },
    },
    required=("skill_name", "command"),
    progress="⚙️ 正在执行技能《{skill_name}》命令…",
)
def run_skill_command(ctx: SessionContext, emitter, skill_name: str, command: list[str], timeout: int = _CMD_TIMEOUT) -> ToolResult:
    if not isinstance(command, list) or not command:
        return _ok({"error": "invalid_command"})
    if not ctx.skills_root:
        return _ok({"error": "no_skills_root"})
    skill_dir = safe_join(ctx.skills_root, skill_name)
    if not os.path.isdir(skill_dir):
        return _ok({"error": "skill_not_found", "skill": skill_name})
    argv = [str(c) for c in command]
    # python家族统一换成运行时解释器（安全闸只认解释器路径）
    if argv[0].lower() in ("python", "python3", "python3.10", "python3.11", "python3.12"):
        argv = [sys.executable] + argv[1:]
    verdict = exec_policy.resolve_and_validate_exec(
        command=argv,
        session_dir=ctx.workspace_root or os.getcwd(),
        skills_root=None,  # 解释器在系统路径，不再按skills_root拦截
    )
    if not verdict.get("ok"):
        return _ok({"error": verdict.get("error", "exec_denied"), "detail": verdict.get("detail") or ""})
    before = {e["relative_path"] for e in list_dir(skill_dir, max_depth=3) if e["type"] == "file"}
    try:
        proc = subprocess.run(
            verdict["argv"], cwd=skill_dir, capture_output=True, text=True,
            timeout=max(1, min(int(timeout), _CMD_TIMEOUT)),
        )
    except subprocess.TimeoutExpired:
        return _ok({"error": "timeout"})
    except Exception as e:
        return _ok({"error": "exec_failed", "detail": str(e)})
    # 收割技能目录内新产物到工作区
    collected: list[str] = []
    out_dir = os.path.join(ctx.workspace_root or os.getcwd(), "skill_outputs")
    os.makedirs(out_dir, exist_ok=True)
    import shutil as _shutil

    for e in list_dir(skill_dir, max_depth=3):
        if e["type"] == "file" and e["relative_path"] not in before:
            dest = os.path.join(out_dir, os.path.basename(e["path"]))
            try:
                _shutil.copy2(e["path"], dest)
                collected.append(f"skill_outputs/{os.path.basename(e['path'])}")
            except Exception:
                pass
    return _ok({
        "code": proc.returncode,
        "stdout": shorten_text(proc.stdout, _MAX_CMD_OUTPUT),
        "stderr": shorten_text(proc.stderr, 4000) if proc.stderr.strip() else "",
        "collected_files": collected[:20],
    })


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
