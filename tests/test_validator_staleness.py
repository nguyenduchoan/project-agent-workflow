#!/usr/bin/env python3
"""Deterministic architecture-record staleness tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


class StalenessTests(unittest.TestCase):
    @staticmethod
    def branch_record(
        repo: ValidatorRepo,
        *,
        branch: str = "main",
        verified_at: str = "2026-09-04",
        stale_after: str = "30",
    ) -> str:
        slug = branch.replace("/", "-")
        return f"""# Branch architecture

- Branch: {branch}
- Slug: {slug}
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
"""

    @staticmethod
    def historical_task(repo: ValidatorRepo) -> str:
        return f"""# Historical task

- ID: 20260101-retired
- Status: done
- Delivery gate: passed
- Owner: test
- Reviewer: test
- Created: 2026-01-01
- Updated: 2026-01-02
- Branch: retired
- Base ref / merge-base: {repo.head}
- Source commit: {repo.head}
- Affected paths: retired/**
- Architecture impact: architecture-change
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""

    @staticmethod
    def historical_change(repo: ValidatorRepo) -> str:
        return f"""# Historical architecture change

- ID: 20260101-retired-change
- Date: 2026-01-02
- Branch: retired
- Base ref / merge-base: {repo.head}
- Head snapshot: {repo.head}
- Related task: 20260101-retired
- Status: superseded
- Delivery gate: passed
- Source commit: {repo.head}
- Verified at: 2026-01-02
- Stale after days: 30
- Affected paths: retired/**
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""

    @staticmethod
    def current_task(repo: ValidatorRepo) -> str:
        return f"""# Current task

- ID: 20260904-current
- Status: in-progress
- Delivery gate: pending
- Owner: test
- Reviewer: test
- Created: 2026-09-04
- Updated: 2026-09-04
- Branch: main
- Base ref / merge-base: {repo.head}
- Source commit: {repo.head}
- Affected paths: config/**
- Architecture impact: none
- Related architecture records: 20260904-current-change
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""

    @staticmethod
    def current_change(repo: ValidatorRepo) -> str:
        return f"""# Current architecture change

- ID: 20260904-current-change
- Date: 2026-01-01
- Branch: main
- Base ref / merge-base: {repo.head}
- Head snapshot: {repo.head}
- Related task: 20260904-current
- Status: accepted
- Delivery gate: passed
- Source commit: {repo.head}
- Verified at: 2026-01-01
- Stale after days: 30
- Affected paths: config/**
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""

    def validate_record(self, verified_at: str, stale_after: str):
        repo = ValidatorRepo("main")
        self.addCleanup(repo.close)
        repo.write(
            ".agents/architecture/branches/main.md",
            self.branch_record(
                repo,
                verified_at=verified_at,
                stale_after=stale_after,
            ),
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

    def test_stale_unrelated_branch_record_is_a_warning(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/feature-login.md",
                self.branch_record(
                    repo,
                    branch="feature/login",
                    verified_at="2026-01-01",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("WARNING: [record-stale]", result.stdout)
            self.assertIn("historical architecture record", result.stdout)

    def test_stale_other_active_branch_record_is_a_warning(self) -> None:
        with ValidatorRepo("main") as repo:
            other_task = (
                self.current_task(repo)
                .replace("20260904-current", "20260904-login")
                .replace("Branch: main", "Branch: feature/login")
                .replace(
                    "Related architecture records: 20260904-login-change",
                    "Related architecture records: feature-login",
                )
            )
            repo.write(
                ".agents/tasks/active/20260904-login.md",
                other_task,
            )
            repo.write(
                ".agents/architecture/branches/feature-login.md",
                self.branch_record(
                    repo,
                    branch="feature/login",
                    verified_at="2026-01-01",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("WARNING: [record-stale]", result.stdout)
            self.assertIn("other active branch architecture record", result.stdout)

    def test_stale_historical_change_record_is_a_warning(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/history/completed/20260101-retired.md",
                self.historical_task(repo),
            )
            repo.write(
                ".agents/architecture/changes/20260101-retired-change.md",
                self.historical_change(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("WARNING: [record-stale]", result.stdout)
            self.assertIn("historical architecture record", result.stdout)

    def test_stale_change_linked_from_current_task_remains_an_error(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-current.md",
                self.current_task(repo),
            )
            repo.write(
                ".agents/architecture/changes/20260904-current-change.md",
                self.current_change(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-stale]", result.stdout)
            self.assertIn("linked architecture record", result.stdout)

    def test_malformed_date_in_historical_record_remains_an_error(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/retired.md",
                self.branch_record(
                    repo,
                    branch="retired",
                    verified_at="2026-02-30",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("Verified at must be an ISO date", result.stdout)

    def test_invalid_stale_period_in_historical_record_remains_an_error(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/retired.md",
                self.branch_record(
                    repo,
                    branch="retired",
                    stale_after="invalid",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-stale-threshold]", result.stdout)

    def test_invalid_today_override_is_configuration_error(self) -> None:
        with ValidatorRepo("main") as repo:
            result = repo.run_validator("--today", "2026-02-30")
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("--today must be an ISO date", result.stderr)


if __name__ == "__main__":
    unittest.main()
