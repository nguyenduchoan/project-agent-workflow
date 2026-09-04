#!/usr/bin/env python3
"""Architecture gate base-resolution and opt-out tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from validator_test_support import PROJECT_TEMPLATE, VALIDATOR, ValidatorRepo


class ArchitectureGateTests(unittest.TestCase):
    def assert_sensitive_gate_ran(self, repo: ValidatorRepo, expected_source: str) -> None:
        repo.write("config/application.yml", "enabled: true\n")
        result = repo.run_validator("--today", "2026-09-04")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(expected_source, result.stdout)
        self.assertIn("ERROR [architecture-registry-missing]", result.stdout)

    def test_auto_resolves_upstream_merge_base(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.git("checkout", "-qb", "feature")
            repo.git("branch", "--set-upstream-to=main", "feature")
            self.assert_sensitive_gate_ran(repo, "upstream tracking branch merge-base")

    def test_auto_resolves_origin_main(self) -> None:
        with ValidatorRepo("topic") as repo:
            repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
            self.assert_sensitive_gate_ran(repo, "resolved from origin/main")

    def test_auto_resolves_local_main(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.git("checkout", "-qb", "topic")
            self.assert_sensitive_gate_ran(repo, "resolved from main")

    def test_missing_base_warns_but_structural_validation_succeeds(self) -> None:
        with ValidatorRepo("topic") as repo:
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn(
                "WARNING: architecture gate skipped: no safe base reference could be resolved",
                result.stdout,
            )

    def test_unrelated_fallback_history_is_not_selected(self) -> None:
        with ValidatorRepo("topic") as repo:
            unrelated = repo.git(
                "commit-tree",
                repo.git("write-tree"),
                "-m",
                "unrelated root",
            )
            repo.git("update-ref", "refs/remotes/origin/main", unrelated)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn(
                "WARNING: architecture gate skipped: no safe base reference could be resolved",
                result.stdout,
            )

    def test_explicit_base_overrides_invalid_configured_base(self) -> None:
        with ValidatorRepo("topic") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["base_ref"] = "does-not-exist"
            repo.write_policy(policy)
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("resolved from explicit --base-ref: HEAD", result.stdout)

    def test_configured_base_precedes_fallbacks(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["base_ref"] = "HEAD"
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("registry policy architecture_gate.base_ref: HEAD", result.stdout)

    def test_explicit_opt_out_skips_only_architecture_diff(self) -> None:
        with ValidatorRepo("topic") as repo:
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("disabled by explicit --no-architecture-gate", result.stdout)

            repo.write(
                ".agents/tasks/active/broken.md",
                "# Broken\n\n- ID: broken\n- Status: invalid\n",
            )
            invalid = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, invalid.returncode, invalid.stdout + invalid.stderr)
            self.assertIn("ERROR [task-metadata]", invalid.stdout)

    def test_non_git_structural_validation_continues(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="project-agent-non-git-test."
        ) as temporary:
            root = Path(temporary)
            shutil.copytree(PROJECT_TEMPLATE, root / ".agents")
            result = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--project",
                    str(root),
                    "--today",
                    "2026-09-04",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("architecture gate skipped", result.stdout)
            self.assertIn("Git metadata object validation skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
