"""Skill manager tool — parameterized commands (no more Chinese-regex parsing).

mini_claw's TM tool asked the LLM to compose Chinese command strings like
"删除技能2" and parsed them with regex. Here every command is a typed form
field the workflow fills deterministically.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Generator
from typing import Any

from core import log
from core.skills.packages import list_installed, parse_manifest
from core.util import safe_get, shorten_text
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin import Tool


class SkillManagerTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        action = str(tool_parameters.get("action") or "list").strip().lower()
        if action not in {"list", "install", "remove", "download"}:
            yield self.create_text_message("❌无效操作：" + action + "（仅支持 list / install / remove / download）")
            return
        skills_root = str(tool_parameters.get("skills_root") or "") or os.environ.get("SKILLS_ROOT") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"
        )
        try:
            if action == "list":
                yield from self._list(skills_root)
            elif action == "install":
                yield from self._install(skills_root, tool_parameters.get("files"))
            elif action == "remove":
                yield from self._remove(skills_root, int(tool_parameters.get("index") or 0))
            elif action == "download":
                yield from self._download(skills_root, int(tool_parameters.get("index") or 0))
            else:
                yield self.create_text_message(f"❌未知操作：{action}（支持 list / install / remove / download）")
        except Exception as e:
            log.error("skill_manager_failed", action=action, detail=str(e))
            yield self.create_text_message(f"❌技能管理操作失败：{e}")

    # ── actions ──────────────────────────────────────────────────────────

    def _list(self, skills_root: str) -> Generator[ToolInvokeMessage, None, None]:
        skills = list_installed(skills_root)
        if not skills:
            yield self.create_text_message("当前没有已安装的技能包。")
            return
        lines = ["👓当前技能列表：", ""]
        for i, s in enumerate(skills, 1):
            if s.get("invalid_reason") and not s.get("version"):
                lines.append(f"{i}. {s['name']} 🔴无效（{s['invalid_reason']}）")
                continue
            icon = "🟢可用" if s.get("eligible") else "🔴缺依赖"
            extra = []
            for k in ("missing_bins", "missing_py", "missing_env"):
                if s.get(k):
                    extra.append(f"{k}={','.join(s[k][:4])}")
            note = f"（缺：{'；'.join(extra)}）" if extra else ""
            lines.append(f"{i}. {s['name']} v{s.get('version', '0')} {icon}{note}")
        yield self.create_text_message("\n".join(lines))

    def _install(self, skills_root: str, files: Any) -> Generator[ToolInvokeMessage, None, None]:
        if not files:
            yield self.create_text_message("❌未收到技能包文件：请上传 zip 后再执行 install。")
            return
        os.makedirs(skills_root, exist_ok=True)
        from core.net import download_file_bytes

        installed: list[str] = []
        for f in files if isinstance(files, list) else [files]:
            url = safe_get(f, "url")
            filename = str(safe_get(f, "filename") or "skill.zip")
            if not url:
                continue
            try:
                data = download_file_bytes(str(url))
                name = self._install_zip(skills_root, data)
                installed.append(name)
            except Exception as e:
                yield self.create_text_message(f"❌安装 {filename} 失败：{e}")
        if installed:
            yield self.create_text_message(f"✅技能已安装：{', '.join(installed)}")
            yield from self._list(skills_root)

    def _install_zip(self, skills_root: str, data: bytes) -> str:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "pack.zip")
            with open(zip_path, "wb") as f:
                f.write(data)
            with zipfile.ZipFile(zip_path) as z:
                root_dir = self._locate_skill_root(z)
                if root_dir is None:
                    raise ValueError("压缩包内未找到 SKILL.md")
                target = os.path.join(skills_root, os.path.basename(root_dir) or "unnamed-skill")
                if os.path.isdir(target):
                    shutil.rmtree(target)
                os.makedirs(target, exist_ok=True)
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    if root_dir and name.startswith(root_dir + "/"):
                        rel = name[len(root_dir) + 1:]
                    else:
                        rel = name
                    dest = self._safe_dest(target, rel)
                    if dest is None:
                        continue
                    os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
                    with z.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
            manifest, err = parse_manifest(target)
            if manifest is None:
                shutil.rmtree(target, ignore_errors=True)
                raise ValueError(f"SKILL.md 校验失败：{err}")
            return os.path.basename(target)

    @staticmethod
    def _locate_skill_root(z: zipfile.ZipFile) -> str | None:
        names = [n.replace("\\", "/") for n in z.namelist()]
        for n in names:
            if n == "SKILL.md" or n.endswith("/SKILL.md"):
                return n[:-len("/SKILL.md")] if n != "SKILL.md" else ""
        return None

    @staticmethod
    def _safe_dest(target: str, rel: str) -> str | None:
        dest = os.path.abspath(os.path.join(target, rel))
        if os.path.commonpath([os.path.abspath(target), dest]) != os.path.abspath(target):
            return None  # zip-slip guard
        return dest

    def _remove(self, skills_root: str, index: int) -> Generator[ToolInvokeMessage, None, None]:
        skills = sorted(d for d in os.listdir(skills_root) if os.path.isdir(os.path.join(skills_root, d))) if os.path.isdir(skills_root) else []
        if index < 1 or index > len(skills):
            yield self.create_text_message(f"❌序号无效：{index}（当前共 {len(skills)} 个技能）")
            return
        name = skills[index - 1]
        shutil.rmtree(os.path.join(skills_root, name), ignore_errors=True)
        yield self.create_text_message(f"✅已删除技能：{name}")
        yield from self._list(skills_root)

    def _download(self, skills_root: str, index: int) -> Generator[ToolInvokeMessage, None, None]:
        skills = sorted(d for d in os.listdir(skills_root) if os.path.isdir(os.path.join(skills_root, d))) if os.path.isdir(skills_root) else []
        if index < 1 or index > len(skills):
            yield self.create_text_message(f"❌序号无效：{index}（当前共 {len(skills)} 个技能）")
            return
        name = skills[index - 1]
        import io

        buf = io.BytesIO()
        src_dir = os.path.join(skills_root, name)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for current_root, _dirs, files in os.walk(src_dir):
                for fn in files:
                    full = os.path.join(current_root, fn)
                    z.write(full, os.path.join(name, os.path.relpath(full, src_dir)))
        yield self.create_blob_message(
            blob=buf.getvalue(), meta={"mime_type": "application/zip", "filename": f"{name}.zip"}
        )
        log.info("skill_downloaded", name=name, size=buf.tell())
