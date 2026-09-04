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


def change_record(
    repo: ValidatorRepo,
    related_task: str,
    *,
    record_id: str = "20260904-change",
    affected: str = "docs/**",
) -> str:
    return f"""# Architecture change

- ID: {record_id}
- Date: 2026-09-04
- Branch: main
- Base ref / merge-base: {repo.head}
- Head snapshot: {repo.head}
- Related task: {related_task}
- Status: accepted
- Delivery gate: passed
- Source commit: {repo.head}
- Verified at: 2026-09-04
- Stale after days: 30
- Affected paths: {affected}
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


def standard_task_with_architecture(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-demo",
    related_records: str = "none",
    impact: str = "none",
    branch: str = "main",
    affected: str = "docs/**",
) -> str:
    return f"""# Standard task

- ID: {task_id}
- Mode: STANDARD
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: linked architecture evidence is valid
- Validation: validator tests
- Architecture impact: {impact}
- Branch: {branch}
- Base ref: {repo.head}
- Source commit: {repo.head}
- Risks: bounded
- Dependencies: none
- Related architecture records: {related_records}
- Review notes: reviewed
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

    def test_other_active_task_branch_skips_checkout_relative_checks(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo, branch="other"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertNotIn("ERROR [record-branch]", result.stdout)

    def test_current_and_other_branch_tasks_can_coexist(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-current.md",
                active_task(repo, task_id="20260904-current"),
            )
            repo.write(
                ".agents/tasks/active/20260904-other.md",
                active_task(
                    repo,
                    task_id="20260904-other",
                    branch="feature/login",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_two_tasks_on_current_branch_are_both_supported(self) -> None:
        with ValidatorRepo("main") as repo:
            for task_id in ("20260904-first", "20260904-second"):
                repo.write(
                    f".agents/tasks/active/{task_id}.md",
                    active_task(repo, task_id=task_id),
                )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_other_branch_task_may_have_an_older_current_head(self) -> None:
        with ValidatorRepo("main") as repo:
            previous_head = repo.head
            repo.write("tracked.txt", "next\n")
            repo.git("add", "tracked.txt")
            repo.git("commit", "-qm", "next")
            repo.write(
                ".agents/tasks/active/20260904-other.md",
                active_task(
                    repo,
                    task_id="20260904-other",
                    branch="feature/login",
                )
                + f"- Current head: {previous_head}\n",
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertNotIn("ERROR [record-current-head]", result.stdout)

    def test_invalid_structure_in_other_branch_task_still_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            invalid = active_task(repo, branch="feature/login").replace(
                f"- Source commit: {repo.head}\n", ""
            )
            repo.write(".agents/tasks/active/20260904-demo.md", invalid)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [task-metadata]", result.stdout)
            self.assertIn("missing metadata: Source commit", result.stdout)

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

    def test_detached_head_skips_checkout_relative_current_head_check(self) -> None:
        with ValidatorRepo("main") as repo:
            previous_head = repo.head
            repo.write("tracked.txt", "next\n")
            repo.git("add", "tracked.txt")
            repo.git("commit", "-qm", "next")
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                active_task(repo) + f"- Current head: {previous_head}\n",
            )
            repo.git("checkout", "--detach", "-q", "HEAD")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("checkout-relative", result.stdout)
            self.assertNotIn("ERROR [record-current-head]", result.stdout)

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
            self.assertIn("WARNING: [architecture-link-mismatch]", result.stdout)
            self.assertIn("legacy task link is not bidirectional", result.stdout)

    def test_modern_task_must_link_back_to_related_change_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(repo),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "20260904-demo"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-link-mismatch]", result.stdout)
            self.assertIn("does not link to", result.stdout)

    def test_valid_related_architecture_record_path_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/20260904-change.md",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "20260904-demo"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_comma_separated_backticked_architecture_records_pass(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/main.md",
                self.branch_record(repo, repo.head),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "20260904-demo"),
            )
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    impact="confirmed",
                    related_records="`main`, `20260904-change`",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_missing_related_architecture_record_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/missing.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-related-architecture]", result.stdout)
            self.assertIn("does not exist", result.stdout)

    def test_related_architecture_record_traversal_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/../../../outside.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("contains traversal", result.stdout)

    def test_absolute_related_architecture_record_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="/tmp/outside.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("repository registry reference", result.stdout)

    def test_related_architecture_record_outside_managed_directories_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="tasks/active/20260904-demo.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("architecture/branches or architecture/changes", result.stdout)

    def test_related_architecture_record_extension_must_be_markdown(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/change.txt",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("use .md", result.stdout)

    def test_architecture_change_index_is_not_a_record_target(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/index.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-related-architecture]", result.stdout)
            self.assertIn("index is not an architecture record", result.stdout)

    def test_linked_architecture_record_is_still_structurally_validated(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/branches/main.md",
                ),
            )
            repo.write(
                ".agents/architecture/branches/main.md",
                "# Incomplete\n\n- Branch: main\n- Slug: main\n"
                "- Data classification: internal\n"
                "- Provenance: project-authored\n- Executable: false\n",
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-metadata]", result.stdout)

    def test_ambiguous_architecture_record_id_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(repo, related_records="shared"),
            )
            repo.write(
                ".agents/architecture/branches/shared.md",
                self.branch_record(repo, repo.head)
                .replace("Branch: main", "Branch: shared")
                .replace("Slug: main", "Slug: shared"),
            )
            repo.write(
                ".agents/architecture/changes/shared.md",
                change_record(repo, "20260904-demo", record_id="shared"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("is ambiguous", result.stdout)

    def test_symlinked_architecture_record_reference_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            outside = repo.write(
                "outside-change.md",
                change_record(repo, "20260904-demo"),
            )
            target = repo.root / ".agents/architecture/changes/20260904-change.md"
            target.symlink_to(outside)
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    related_records="architecture/changes/20260904-change.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-related-architecture]", result.stdout)
            self.assertIn("must not use a symlink", result.stdout)

    def test_confirmed_impact_requires_a_linked_change_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(repo, impact="confirmed"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-change-evidence]", result.stdout)

    def test_confirmed_impact_rejects_branch_record_only(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/architecture/branches/main.md",
                self.branch_record(repo, repo.head),
            )
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    impact="confirmed",
                    related_records="architecture/branches/main.md",
                ),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("dedicated architecture change record", result.stdout)

    def test_confirmed_change_relationship_must_be_bidirectional(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/tasks/active/20260904-other.md",
                standard_task_with_architecture(repo, task_id="20260904-other"),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, "20260904-other"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-link-mismatch]", result.stdout)

    def test_confirmed_change_with_bidirectional_link_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-demo.md",
                standard_task_with_architecture(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
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
