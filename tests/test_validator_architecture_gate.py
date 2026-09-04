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


def standard_task(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-standard",
    branch: str = "main",
    affected: str = "config/**",
    impact: str = "none",
    related_records: str = "none",
) -> str:
    return f"""# Standard task

- ID: {task_id}
- Mode: STANDARD
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: architecture gate behavior is verified
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


def strict_task(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-strict",
    affected: str = "security/**",
    impact: str = "none",
    related_records: str = "none",
) -> str:
    return f"""# Strict task

- ID: {task_id}
- Mode: STRICT
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: strict architecture gate behavior is verified
- Validation: security and regression tests
- Architecture impact: {impact}
- Branch: main
- Base ref: {repo.head}
- Source commit: {repo.head}
- Risks: security-sensitive
- Dependencies: none
- Related architecture records: {related_records}
- Review notes: reviewed
- Owner: test
- Reviewer: test
- Delivery gate: pending
- Merge-base: {repo.head}
- Current head: {repo.head}
- Rollout: staged
- Rollback: revert the reviewed change
- Evidence: validator tests
- Data classification: internal
- Provenance: project-authored
"""


def branch_record(repo: ValidatorRepo) -> str:
    return f"""# Main branch architecture

- Branch: main
- Slug: main
- Created: 2026-09-04
- Updated: 2026-09-04
- Base ref: {repo.head}
- Merge-base: {repo.head}
- Current head: {repo.head}
- Source commit: {repo.head}
- Verified at: 2026-09-04
- Stale after days: 30
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


def change_record(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-standard",
    record_id: str = "20260904-change",
    branch: str = "main",
    affected: str = "config/**",
    verified_at: str = "2026-09-04",
    stale_after: str | None = "30",
) -> str:
    freshness = f"- Stale after days: {stale_after}\n" if stale_after is not None else ""
    return f"""# Architecture change

- ID: {record_id}
- Date: 2026-09-04
- Branch: {branch}
- Base ref / merge-base: {repo.head}
- Head snapshot: {repo.head}
- Related task: {task_id}
- Status: accepted
- Delivery gate: passed
- Source commit: {repo.head}
- Verified at: {verified_at}
{freshness}- Affected paths: {affected}
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


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

    def test_unchanged_current_branch_task_is_valid_gate_evidence(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo),
            )
            repo.git("add", ".agents")
            repo.git("commit", "-qm", "record task")
            evidence_base = repo.head
            repo.write("config/application.yml", "enabled: true\n")

            result = repo.run_validator(
                "--base-ref", evidence_base, "--today", "2026-09-04"
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("covered by current no-impact task records", result.stdout)

    def test_wrong_branch_task_is_not_gate_evidence(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo, branch="feature/login"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-registry-missing]", result.stdout)
            self.assertIn("current-branch task", result.stdout)

    def test_task_must_cover_the_changed_sensitive_path(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo, affected="docs/**"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-registry-missing]", result.stdout)
            self.assertIn("config/application.yml", result.stdout)

    def test_multiple_current_branch_tasks_can_cover_the_diff_together(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-one.md",
                standard_task(
                    repo,
                    task_id="20260904-one",
                    affected="config/one/**",
                ),
            )
            repo.write(
                ".agents/tasks/active/20260904-two.md",
                standard_task(
                    repo,
                    task_id="20260904-two",
                    affected="config/two/**",
                ),
            )
            repo.write("config/one/application.yml", "one: true\n")
            repo.write("config/two/application.yml", "two: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_changed_branch_record_without_current_task_does_not_pass(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/architecture/branches/main.md", branch_record(repo))
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-registry-missing]", result.stdout)
            self.assertIn("current-branch task", result.stdout)

    def test_possible_impact_accepts_a_valid_changed_branch_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/architecture/branches/main.md", branch_record(repo))
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="possible",
                    related_records="architecture/branches/main.md",
                ),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("validated branch architecture evidence", result.stdout)

    def test_confirmed_impact_rejects_branch_record_only(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/architecture/branches/main.md", branch_record(repo))
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="architecture/branches/main.md",
                ),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-change-evidence]", result.stdout)

    def test_confirmed_impact_accepts_valid_change_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("validated architecture change evidence", result.stdout)

    def test_unchanged_linked_change_record_remains_valid_gate_evidence(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo),
            )
            repo.git("add", ".agents")
            repo.git("commit", "-qm", "record architecture evidence")
            evidence_base = repo.head
            repo.write("config/application.yml", "enabled: true\n")

            result = repo.run_validator(
                "--base-ref", evidence_base, "--today", "2026-09-04"
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("validated architecture change evidence", result.stdout)

    def test_confirmed_change_record_must_cover_sensitive_path(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, affected="docs/**"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-change-evidence]", result.stdout)
            self.assertIn("config/application.yml", result.stdout)

    def test_confirmed_change_record_must_match_current_branch(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, branch="feature/login"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-change-evidence]", result.stdout)
            self.assertIn("current branch", result.stdout)

    def test_gate_evidence_requires_a_stale_threshold(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, stale_after=None),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-evidence-freshness]", result.stdout)

    def test_stale_gate_evidence_is_an_error(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, verified_at="2026-01-01"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-stale]", result.stdout)
            self.assertIn("linked architecture record", result.stdout)

    def test_strict_sensitive_change_requires_change_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-strict.md",
                strict_task(repo),
            )
            repo.write("security/policy.py", "ENABLED = True\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [architecture-change-evidence]", result.stdout)
            self.assertIn("STRICT", result.stdout)

    def test_strict_sensitive_change_accepts_valid_change_record(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-strict.md",
                strict_task(repo, related_records="20260904-strict-change"),
            )
            repo.write(
                ".agents/architecture/changes/20260904-strict-change.md",
                change_record(
                    repo,
                    task_id="20260904-strict",
                    record_id="20260904-strict-change",
                    affected="security/**",
                ),
            )
            repo.write("security/policy.py", "ENABLED = True\n")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_detached_head_skips_branch_specific_architecture_gate(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo),
            )
            repo.write("config/application.yml", "enabled: true\n")
            repo.git("checkout", "--detach", "-q", "HEAD")
            result = repo.run_validator(
                "--base-ref", "HEAD", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn(
                "architecture gate skipped: checkout-relative evidence cannot be selected "
                "because HEAD is detached",
                result.stdout,
            )

    def test_task_affected_paths_reject_traversal(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo, affected="../config/**"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-affected-path]", result.stdout)
            self.assertIn("traversal", result.stdout)

    def test_change_record_affected_paths_reject_absolute_path(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(
                    repo,
                    impact="confirmed",
                    related_records="20260904-change",
                ),
            )
            repo.write(
                ".agents/architecture/changes/20260904-change.md",
                change_record(repo, affected="/tmp/config/**"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [record-affected-path]", result.stdout)
            self.assertIn("repository-relative", result.stdout)

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
