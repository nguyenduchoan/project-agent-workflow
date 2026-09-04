#!/usr/bin/env python3
"""Package metadata and assembled-artifact tests."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from assemble_skill import MANIFEST, PACKAGE_ROOT, SKILL_NAME, assemble, manifest_entries


class PackageTests(unittest.TestCase):
    def test_assembled_artifact_exactly_matches_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="project-agent-artifact-test.") as temporary:
            destination = Path(temporary) / SKILL_NAME
            assemble(destination)
            actual = {
                path.relative_to(destination)
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(set(manifest_entries()), actual)

    def test_frontmatter_declares_runtime_compatibility(self) -> None:
        text = (PACKAGE_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("\n---\n", 1)[0]
        self.assertIn("compatibility: Requires Git, a POSIX shell, and Python 3.10+.", frontmatter)

    def test_implicit_invocation_is_disabled(self) -> None:
        text = (PACKAGE_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", text)

    def test_manifest_is_the_source_manifest(self) -> None:
        self.assertEqual(PACKAGE_ROOT / "skill-manifest.txt", MANIFEST)

    def test_source_version_has_a_changelog_entry(self) -> None:
        version = (PACKAGE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        changelog = (PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {version} - ", changelog)

    def test_unreleased_version_is_not_described_as_a_release(self) -> None:
        version = (PACKAGE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        changelog = (PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {version} - Unreleased", changelog)

    def test_documented_python_commands_use_python3(self) -> None:
        for relative_path in (
            "README.md",
            "SKILL.md",
            "references/registry-workflow.md",
            "SECURITY.md",
        ):
            with self.subTest(path=relative_path):
                text = (PACKAGE_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?m)^\s*python\s+")

    def test_core_ci_runs_on_ubuntu_and_macos(self) -> None:
        workflow = (PACKAGE_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("runs-on: ${{ matrix.os }}", workflow)

    def test_ci_actions_are_pinned_by_full_commit(self) -> None:
        workflow = (PACKAGE_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        actions = re.findall(r"(?m)^\s*- uses:\s+\S+@([^\s#]+)", workflow)
        self.assertTrue(actions)
        for revision in actions:
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_architecture_change_template_includes_freshness_threshold(self) -> None:
        template = (
            PACKAGE_ROOT
            / "assets/project-template/.agents/architecture/templates/architecture-change.md"
        ).read_text(encoding="utf-8")
        self.assertIn("- Stale after days:", template)


if __name__ == "__main__":
    unittest.main()
