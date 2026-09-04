#!/usr/bin/env python3
"""Semantic Git metadata validation tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


def active_task(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-demo",
    branch: str = "main",
) -> str:
    return f"""# Demo task

- ID: {task_id}
- Status: in-progress
- Delivery gate: pending
- Owner: test
- Reviewer: test
- Created: 2026-09-04
- Updated: 2026-09-04
- Branch: {branch}
- Base ref / merge-base: {repo.head}
- Source commit: {repo.head}
- Affected paths: docs/**
- Architecture impact: none
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


def change_record(repo: ValidatorRepo, related_task: str) -> str:
    return f"""# Architecture change

- ID: 20260904-change
- Date: 2026-09-04
- Branch: main
- Base ref / merge-base: {repo.head}
- Head snapshot: {repo.head}
- Related task: {related_task}
- Status: accepted
- Delivery gate: passed
- Source commit: {repo.head}
- Verified at: 2026-09-04
- Affected paths: docs/**
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


class GitMetadataTests(unittest.TestCase):
    def test_valid_source_commit_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_nonexistent_source_commit_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            task = active_task(repo).replace(
                f"Source commit: {repo.head}", "Source commit: deadbeef"
            )
            repo.write(".agents/tasks/active/20260904-demo.md", task)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-source-commit]", result.stdout)
            self.assertIn("does not resolve to a commit", result.stdout)

    def test_nonexistent_base_ref_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            task = active_task(repo).replace(
                f"Base ref / merge-base: {repo.head}",
                "Base ref / merge-base: does-not-exist",
            )
            repo.write(".agents/tasks/active/20260904-demo.md", task)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-base-ref]", result.stdout)
            self.assertIn("does not resolve to a commit", result.stdout)

    def test_active_task_current_head_mismatch_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            previous_head = repo.head
            repo.write("tracked.txt", "next\n")
            repo.git("add", "tracked.txt")
            repo.git("commit", "-qm", "next")
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo) + f"- Current head: {previous_head}\n",
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-current-head]", result.stdout)
            self.assertIn("expected", result.stdout)

    def test_valid_merge_base_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/main.md",
                self.branch_record(repo, repo.head),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_incorrect_merge_base_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            initial = repo.head
            repo.write("tracked.txt", "next\n")
            repo.git("add", "tracked.txt")
            repo.git("commit", "-qm", "next")
            repo.write(
                ".agents/architecture/branches/main.md",
                self.branch_record(repo, initial),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-merge-base]", result.stdout)
            self.assertIn("expected", result.stdout)

    def test_active_task_branch_mismatch_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo, branch="other"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-branch]", result.stdout)

    def test_historical_task_may_reference_an_older_branch(self) -> None:
        with ValidatorRepo("main") as repo:
            task = active_task(repo, branch="retired").replace(
                "Status: in-progress", "Status: done"
            )
            repo.write(
                ".agents/tasks/history/completed/20260904-demo.md",
                task,
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_detached_head_warns_without_branch_mismatch(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo),
            )
            repo.git("checkout", "--detach", "-q", "HEAD")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("HEAD is detached", result.stdout)

    def test_broken_related_task_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "tasks/active/missing.md"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-related-task]", result.stdout)

    def test_related_task_traversal_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "../../outside.md"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("contains traversal", result.stdout)

    def test_symlinked_task_record_is_rejected_without_following_it(self) -> None:
        with ValidatorRepo("main") as repo:
            outside = repo.write("outside-task.md", active_task(repo))
            task = repo.root / ".agents/tasks/active/20260904-demo.md"
            task.symlink_to(outside)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [symlink]", result.stdout)
            self.assertNotIn("ERROR [task-metadata]", result.stdout)

    def test_existing_related_task_id_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "20260904-demo"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    @staticmethod
    def branch_record(repo: ValidatorRepo, merge_base: str) -> str:
        return f"""# Branch architecture

- Branch: main
- Slug: main
- Created: 2026-01-01
- Updated: 2026-09-04
- Base ref: {repo.head}
- Merge-base: {merge_base}
- Current head: {repo.head}
- Source commit: {repo.head}
- Verified at: 2026-09-04
- Stale after days: 30
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


if __name__ == "__main__":
    unittest.main()
