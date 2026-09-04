#!/usr/bin/env python3
"""Built-in and project-specific secret-pattern tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


class SecretPatternTests(unittest.TestCase):
    def test_builtin_high_confidence_pattern_remains_enabled(self) -> None:
        with ValidatorRepo("main") as repo:
            token = "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890"
            repo.write(".agents/reviews/evidence.md", token + "\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("matched forbidden pattern: github-token", result.stdout)

    def test_project_specific_pattern_is_detected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["secret_scan"]["additional_patterns"] = [
                {"id": "project-token", "regex": r"\bACME_[0-9]{8}\b"}
            ]
            repo.write_policy(policy)
            repo.write(".agents/reviews/evidence.md", "ACME_" + "12345678\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("matched forbidden pattern: project-token", result.stdout)

    def test_malformed_project_pattern_is_a_configuration_error(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["secret_scan"]["additional_patterns"] = [
                {"id": "broken", "regex": "("}
            ]
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("secret_scan.additional_patterns.broken.regex is invalid", result.stderr)

    def test_nested_quantifier_pattern_is_rejected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["secret_scan"]["additional_patterns"] = [
                {"id": "unsafe", "regex": "(a+)+$"}
            ]
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("potentially unsafe nested quantifier", result.stderr)

    def test_quantified_alternation_pattern_is_rejected(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["secret_scan"]["additional_patterns"] = [
                {"id": "unsafe-alternation", "regex": "(a|aa)+$"}
            ]
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("potentially unsafe quantified alternation", result.stderr)

    def test_builtin_scanner_cannot_be_disabled(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["secret_scan"]["builtin_patterns"] = False
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("builtin_patterns must remain true", result.stderr)

    def test_mandatory_builtin_definition_cannot_be_removed(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["trust"]["forbidden_patterns"] = []
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("mandatory built-in secret patterns are missing", result.stderr)

    def test_mandatory_builtin_definition_cannot_be_weakened(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["trust"]["forbidden_patterns"][0]["regex"] = "never-match"
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("must not be changed: private-key", result.stderr)

    def test_required_executable_guard_cannot_be_weakened(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["trust"]["required_executable"] = "true"
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("trust.required_executable must remain false", result.stderr)


if __name__ == "__main__":
    unittest.main()
