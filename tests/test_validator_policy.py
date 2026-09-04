#!/usr/bin/env python3
"""Architecture path policy validation tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


class ArchitecturePolicyTests(unittest.TestCase):
    def test_policy_root_must_be_an_object(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write_policy([])  # type: ignore[arg-type]
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("registry policy root must be an object", result.stderr)

    def test_registry_policy_symlink_is_rejected_before_reading(self) -> None:
        with ValidatorRepo("main") as repo:
            policy_path = repo.root / ".agents/policies/registry-policy.json"
            outside = repo.root / "outside-policy.json"
            outside.write_bytes(policy_path.read_bytes())
            policy_path.unlink()
            policy_path.symlink_to(outside)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("registry policy path must not contain a symlink", result.stderr)

    def test_additional_sensitive_glob_triggers_gate(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["additional_sensitive_globs"] = [
                "src/**/domain/**"
            ]
            repo.write_policy(policy)
            repo.write("src/app/domain/model.py", "VALUE = 1\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-registry-missing]", result.stdout)
            self.assertIn("src/app/domain/model.py", result.stdout)

    def test_ignored_glob_suppresses_only_matching_sensitive_path(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["ignored_globs"] = ["config/generated/**"]
            repo.write_policy(policy)
            repo.write("config/generated/application.yml", "generated: true\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("no architecture-sensitive project path changed", result.stdout)

    def test_windows_style_glob_is_normalized(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["additional_sensitive_globs"] = [
                "src\\**\\security\\**"
            ]
            repo.write_policy(policy)
            repo.write("src/app/security/policy.py", "VALUE = 1\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("src/app/security/policy.py", result.stdout)

    def test_absolute_glob_is_rejected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["additional_sensitive_globs"] = [
                "/outside/**"
            ]
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("must be repository-relative", result.stderr)

    def test_windows_drive_qualified_glob_is_rejected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["additional_sensitive_globs"] = [
                "C:outside/**"
            ]
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("must be repository-relative", result.stderr)

    def test_traversal_glob_is_rejected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["ignored_globs"] = ["../config/**"]
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("must not contain traversal", result.stderr)

    def test_branch_history_template_cannot_escape_registry(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["branch_history_template"] = (
                "../outside/{branch_slug}.md"
            )
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("must not contain traversal", result.stderr)

    def test_architecture_gate_enabled_requires_boolean(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["architecture_gate"]["enabled"] = "false"
            repo.write_policy(policy)
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("architecture_gate.enabled must be a boolean", result.stderr)

    def test_legacy_sensitive_globs_remain_supported(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            gate = policy["architecture_gate"]
            gate["sensitive_globs"] = gate.pop("default_sensitive_globs")
            gate.pop("additional_sensitive_globs")
            gate.pop("ignored_globs")
            repo.write_policy(policy)
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-registry-missing]", result.stdout)


if __name__ == "__main__":
    unittest.main()
