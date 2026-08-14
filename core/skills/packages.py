"""Skill package manager — typed manifests and eligibility.

Replaces mini_claw's hand-rolled front-matter parsing and heuristic
"🟡 uncertain" status with strict pydantic validation: a pack is either
valid+eligible (with explicit missing dependencies) or invalid (with the
reason). Compatible with mini_claw/OpenClaw SKILL.md formats.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from core.skills.frontparse import FrontmatterError, split_frontmatter as _split_fm

SKILL_FILE = "SKILL.md"


class SkillRequires(BaseModel):
    bins: list[str] = Field(default_factory=list)
    anyBins: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)
    py: list[str] = Field(default_factory=list)
    js: list[str] = Field(default_factory=list)


class SkillMetaOpenClaw(BaseModel):
    model_config = {"extra": "allow"}
    os: list[str] = Field(default_factory=list)
    requires: SkillRequires = Field(default_factory=SkillRequires)


class SkillManifest(BaseModel):
    """Strongly typed SKILL.md front-matter."""

    model_config = {"extra": "allow"}

    name: str
    description: str = ""
    version: str = "0.0.0"
    read_when: str = Field(default="", alias="read-when")

    # visibility / invocation gates (mini_claw compat)
    user_invocable: bool = Field(default=True, alias="user-invocable")
    disable_model_invocation: bool = Field(default=False, alias="disable-model-invocation")
    allowed_tools: str = Field(default="", alias="allowed-tools")

    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def openclaw(self) -> SkillMetaOpenClaw | None:
        raw = self.metadata.get("openclaw")
        if not isinstance(raw, dict):
            return None
        try:
            return SkillMetaOpenClaw.model_validate(raw)
        except ValidationError:
            return None

    @property
    def requires(self) -> SkillRequires:
        oc = self.openclaw
        return oc.requires if oc is not None else SkillRequires()

    @property
    def supported_os(self) -> list[str]:
        oc = self.openclaw
        return oc.os if oc is not None else []


class SkillStatus(BaseModel):
    name: str
    version: str
    eligible: bool
    missing_bins: list[str] = Field(default_factory=list)
    missing_any_bins: list[str] = Field(default_factory=list)
    missing_env: list[str] = Field(default_factory=list)
    missing_py: list[str] = Field(default_factory=list)
    invalid_reason: str = ""


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, body) via the stdlib subset parser."""
    return _split_fm(content)


def parse_manifest(skill_dir: str) -> tuple[SkillManifest | None, str]:
    path = os.path.join(skill_dir, SKILL_FILE)
    if not os.path.isfile(path):
        return None, f"missing {SKILL_FILE}"
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(200000)
    fm, _body = split_frontmatter(content)
    if not fm:
        return None, "empty or unparsable frontmatter"
    try:
        manifest = SkillManifest.model_validate(fm)
    except ValidationError as e:
        return None, f"manifest invalid: {e.error_count()} errors (first: {e.errors()[0].get('msg')})"
    return manifest, ""


def check_eligibility(manifest: SkillManifest) -> SkillStatus:
    missing_bins = [b for b in manifest.requires.bins if not shutil.which(b)]
    missing_any = []
    if manifest.requires.anyBins and not any(shutil.which(b) for b in manifest.requires.anyBins):
        missing_any = list(manifest.requires.anyBins)
    missing_env = [k for k in manifest.requires.env if not os.environ.get(k)]
    missing_py: list[str] = []
    if manifest.requires.py:
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        for dep in manifest.requires.py[:30]:
            try:
                pkg_version(dep)
            except PackageNotFoundError:
                missing_py.append(dep)
    current_os = os.name  # posix / nt
    os_ok = not manifest.supported_os or current_os in manifest.supported_os or "linux" in manifest.supported_os
    eligible = not (missing_bins or missing_any or missing_env or missing_py) and os_ok
    reason = "" if eligible else "unsupported_os" if not os_ok else ""
    return SkillStatus(
        name=manifest.name,
        version=manifest.version,
        eligible=eligible,
        missing_bins=missing_bins,
        missing_any_bins=missing_any,
        missing_env=missing_env,
        missing_py=missing_py,
        invalid_reason=reason,
    )


def list_installed(skills_root: str) -> list[dict[str, Any]]:
    """Inventory installed packs with eligibility (deterministic status).

    ``name`` is the directory name — the stable key tools use to reference
    the pack; the manifest's own name is reported as ``manifest_name``.
    """
    if not skills_root or not os.path.isdir(skills_root):
        return []
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(skills_root)):
        d = os.path.join(skills_root, name)
        if not os.path.isdir(d):
            continue
        manifest, err = parse_manifest(d)
        if manifest is None:
            out.append({"name": name, "eligible": False, "invalid_reason": err})
            continue
        status = check_eligibility(manifest)
        payload = status.model_dump()
        payload["name"] = name
        payload["manifest_name"] = manifest.name
        out.append(payload)
    return out


def find_skill_dir(skills_root: str, name: str) -> str | None:
    candidate = os.path.join(skills_root, name)
    return candidate if os.path.isdir(candidate) else None
