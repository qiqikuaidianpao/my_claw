"""Skill package manager tests (pydantic manifests, eligibility)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skills.packages import check_eligibility, list_installed, parse_manifest, split_frontmatter

GOOD = """---
name: rental-calc-skill
description: Rental repayment schedule calculator.
version: 1.0.0
read-when: When the user asks for a repayment plan.
metadata:
  openclaw:
    os: [linux, win32]
    requires:
      bins: [python3]
---

# Rental Calculation Skill

Body instructions here.
"""

RICH_LISTS = """---
name: chart-skill
description: |
  Multi-line description supported.
version: "2.0"
metadata:
  openclaw:
    requires:
      anyBins: [python3, python]
      py: [numpy]
---
Body.
"""


class TestManifestParsing(unittest.TestCase):
    def test_split_frontmatter(self):
        fm, body = split_frontmatter(GOOD)
        self.assertEqual(fm["name"], "rental-calc-skill")
        self.assertIn("Rental Calculation Skill", body)

    def test_rich_yaml_supported(self):
        fm, _ = split_frontmatter(RICH_LISTS)
        self.assertEqual(fm["version"], "2.0")
        reqs = fm["metadata"]["openclaw"]["requires"]
        self.assertEqual(reqs["anyBins"], ["python3", "python"])

    def test_parse_manifest_ok(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(GOOD)
            manifest, err = parse_manifest(d)
            self.assertEqual(err, "")
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest.read_when, "When the user asks for a repayment plan.")
            self.assertEqual(manifest.requires.bins, ["python3"])

    def test_parse_manifest_missing(self):
        with tempfile.TemporaryDirectory() as d:
            manifest, err = parse_manifest(d)
            self.assertIsNone(manifest)
            self.assertIn("missing", err)

    def test_parse_manifest_invalid(self):
        bad = "---\ndescription: no name here\n---\nbody"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(bad)
            manifest, err = parse_manifest(d)
            self.assertIsNone(manifest)
            self.assertIn("invalid", err)


class TestEligibility(unittest.TestCase):
    def test_python3_bin_ok(self):
        fm, _ = split_frontmatter(GOOD)
        from core.skills.packages import SkillManifest

        m = SkillManifest.model_validate(fm)
        status = check_eligibility(m)
        # python3 may or may not exist on this host; assert consistency
        import shutil

        if shutil.which("python3"):
            self.assertTrue(status.eligible)
        else:
            self.assertEqual(status.missing_bins, ["python3"])

    def test_missing_env(self):
        fm, _ = split_frontmatter(GOOD)
        fm.setdefault("metadata", {})["openclaw"] = {"requires": {"env": ["DEFINITELY_NOT_SET_VAR_XYZ"]}}
        from core.skills.packages import SkillManifest

        m = SkillManifest.model_validate(fm)
        status = check_eligibility(m)
        self.assertFalse(status.eligible)
        self.assertIn("DEFINITELY_NOT_SET_VAR_XYZ", status.missing_env)


class TestListInstalled(unittest.TestCase):
    def test_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            good = os.path.join(root, "alpha")
            os.makedirs(good)
            with open(os.path.join(good, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(GOOD)
            broken = os.path.join(root, "broken")
            os.makedirs(broken)
            result = list_installed(root)
            names = [r["name"] for r in result]
            self.assertEqual(names, ["alpha", "broken"])
            self.assertIn("eligible", result[0])
            self.assertEqual(result[0]["description"], "Rental repayment schedule calculator.")
            self.assertTrue(result[1]["invalid_reason"])

    def test_empty_root(self):
        self.assertEqual(list_installed(""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
