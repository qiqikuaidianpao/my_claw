"""agentskills.io open-standard compatibility tests (0.6.0 F2)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.skills.packages import (
    check_eligibility,
    list_installed,
    parse_manifest,
    sanitize_skill_dirname,
)

FIXTURES = Path(__file__).parent / "fixtures" / "skills_std"


def test_minimal_manifest_only_name_description():
    """agentskills.io spec: only name + description required → 🟢 eligible."""
    with tempfile.TemporaryDirectory() as d:
        skill_dir = os.path.join(d, "pdf-summary")
        shutil.copytree(FIXTURES / "pdf-summary", skill_dir)
        manifest, err = parse_manifest(skill_dir)
        assert manifest is not None, err
        assert manifest.name == "pdf-summary"
        # unknown fields pass through
        assert "license" in manifest.model_extra or manifest.model_config["extra"] == "allow"
        # read-when absent → description doubles as trigger hint
        assert manifest.trigger_hint.startswith("Extract key points")
        # no name warnings for a clean slug
        assert manifest.name_warnings == []
        status = check_eligibility(manifest)
        assert status.eligible, status.invalid_reason


def test_uppercase_name_warns_and_sanitize():
    """name='Report Generator' → warnings, dir sanitized to report-generator."""
    with tempfile.TemporaryDirectory() as d:
        skill_dir = os.path.join(d, "Report Generator")
        shutil.copytree(FIXTURES / "Report Generator", skill_dir)
        manifest, err = parse_manifest(skill_dir)
        assert manifest is not None, err
        assert manifest.name == "Report Generator"
        # spec constraint reported as warning, not rejection
        assert any("lowercase" in w for w in manifest.name_warnings)
        # openclaw metadata still parsed
        assert manifest.requires.bins == ["python3"]
        assert set(manifest.supported_os) == {"linux", "win32"}
        # eligibility still computed from requires
        status = check_eligibility(manifest)
        # python3 present on test hosts; os gate passes (linux listed)
        assert status.eligible or "unsupported_os" == status.invalid_reason


def test_sanitize_dirname():
    assert sanitize_skill_dirname("Report Generator") == "report-generator"
    assert sanitize_skill_dirname("My Cool Skill!") == "my-cool-skill"
    assert sanitize_skill_dirname("--leading--") == "leading"
    assert sanitize_skill_dirname("已有中文技能") == "skill"  # non-ascii collapses to fallback
    assert sanitize_skill_dirname("") == "skill"
    long_name = "a" * 100
    assert len(sanitize_skill_dirname(long_name)) == 64


def test_list_installed_reports_std_packs():
    """Fixture directory with both formats → both listed with description."""
    with tempfile.TemporaryDirectory() as root:
        shutil.copytree(FIXTURES / "pdf-summary", os.path.join(root, "pdf-summary"))
        shutil.copytree(FIXTURES / "Report Generator", os.path.join(root, "Report Generator"))
        items = list_installed(root)
        by_name = {i["name"]: i for i in items}
        assert "pdf-summary" in by_name
        assert "Report Generator" in by_name
        assert by_name["pdf-summary"]["description"].startswith("Extract key points")
        assert by_name["Report Generator"]["description"].startswith("Build a formatted")


def test_old_openclaw_format_still_works():
    """Regression: packs with read-when/metadata.requires continue to work."""
    with tempfile.TemporaryDirectory() as d:
        skill_dir = os.path.join(d, "old-style")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: old-style\n"
                "description: legacy pack with full frontmatter\n"
                "version: 1.0.0\n"
                "read-when: 当用户提到老式技能\n"
                "metadata:\n"
                "  openclaw:\n"
                "    os: [linux, win32]\n"
                "    requires:\n"
                "      bins: []\n"
                "---\n"
                "# Old style\nBody here.\n"
            )
        manifest, err = parse_manifest(skill_dir)
        assert manifest is not None, err
        assert manifest.read_when == "当用户提到老式技能"
        assert manifest.trigger_hint == "当用户提到老式技能"
        assert manifest.version == "1.0.0"


def test_zip_install_sanitizes_dirname():
    """Simulated install: zip with 'My Skill' folder lands as my-skill."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "pack.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("My Skill/SKILL.md", "---\nname: my-skill\ndescription: x\n---\nbody")
        skills_root = os.path.join(tmp, "skills")
        os.makedirs(skills_root)
        with zipfile.ZipFile(zip_path) as z:
            names = [n.replace("\\", "/") for n in z.namelist()]
            root_dir = next(n[: -len("/SKILL.md")] for n in names if n.endswith("/SKILL.md"))
            target = os.path.join(skills_root, sanitize_skill_dirname(os.path.basename(root_dir)))
            os.makedirs(target)
            z.extractall(tmp)
            shutil.rmtree(target)
            shutil.copytree(os.path.join(tmp, "My Skill"), target)
        # the sanitized dir parses fine
        manifest, err = parse_manifest(os.path.join(skills_root, "my-skill"))
        assert manifest is not None, err
