#!/usr/bin/env python3
"""Deterministic architecture-record staleness tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


class StalenessTests(unittest.TestCase):
    def validate_record(self, verified_at: str, stale_after: str):
        repo = ValidatorRepo("main")
        self.addCleanup(repo.close)
        repo.write(
            ".agents/architecture/branches/main.md",
            f"""# Main branch architecture

- Branch: main
- Slug: main
- Created: 2026-01-01
- Updated: 2026-09-04
- Base ref: {repo.head}
- Merge-base: {repo.head}
- Current head: {repo.head}
- Source commit: {repo.head}
- Verified at: {verified_at}
- Stale after days: {stale_after}
- Data classification: internal
- Provenance: project-authored
- Executable: false
""",
        )
        return repo.run_validator(
            "--no-architecture-gate", "--today", "2026-09-04"
        )

    def test_fresh_record_passes(self) -> None:
        result = self.validate_record("2026-08-06", "30")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_record_exactly_at_threshold_passes(self) -> None:
        result = self.validate_record("2026-08-05", "30")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_record_one_day_over_threshold_fails(self) -> None:
        result = self.validate_record("2026-08-04", "30")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("ERROR [record-stale]", result.stdout)
        self.assertIn("31 days > 30 days", result.stdout)

    def test_future_verified_date_fails(self) -> None:
        result = self.validate_record("2026-09-05", "30")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("Verified at must not be in the future", result.stdout)

    def test_invalid_verified_date_fails(self) -> None:
        result = self.validate_record("2026-02-30", "30")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("Verified at must be an ISO date", result.stdout)

    def test_non_positive_stale_period_fails(self) -> None:
        result = self.validate_record("2026-09-04", "-1")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("ERROR [record-stale-threshold]", result.stdout)

    def test_invalid_today_override_is_configuration_error(self) -> None:
        with ValidatorRepo("main") as repo:
            result = repo.run_validator("--today", "2026-02-30")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("--today must be an ISO date", result.stderr)


if __name__ == "__main__":
    unittest.main()
