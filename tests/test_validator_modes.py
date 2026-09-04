#!/usr/bin/env python3
"""Workflow mode validation and escalation tests."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


def light_task(task_id: str = "20260904-light", affected: str = "docs/**") -> str:
    return f"""# Light task

- ID: {task_id}
- Mode: LIGHT
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: focused behavior is verified
- Validation: unit test pending
- Architecture impact: none
"""


def standard_task(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-standard",
    affected: str = "docs/**",
) -> str:
    return f"""# Standard task

- ID: {task_id}
- Mode: STANDARD
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: behavior is verified
- Validation: unit and integration tests pending
- Architecture impact: none
- Branch: main
- Base ref: {repo.head}
- Source commit: {repo.head}
- Risks: bounded
- Dependencies: none
- Related architecture records: none
- Review notes: pending
"""


def strict_task(
    repo: ValidatorRepo,
    *,
    task_id: str = "20260904-strict",
    affected: str = "security/**",
) -> str:
    return f"""# Strict task

- ID: {task_id}
- Mode: STRICT
- Status: in-progress
- Created: 2026-09-04
- Updated: 2026-09-04
- Affected paths: {affected}
- Acceptance criteria: security behavior is verified
- Validation: security and regression tests pending
- Architecture impact: none
- Branch: main
- Base ref: {repo.head}
- Source commit: {repo.head}
- Risks: security-sensitive
- Dependencies: none
- Related architecture records: none
- Review notes: pending
- Owner: test
- Reviewer: test
- Delivery gate: pending
- Merge-base: {repo.head}
- Current head: {repo.head}
- Rollout: staged
- Rollback: revert the reviewed change
- Evidence: tests pending
- Data classification: internal
- Provenance: project-authored
"""


def legacy_task(repo: ValidatorRepo) -> str:
    return f"""# Legacy task

- ID: 20260904-legacy
- Status: in-progress
- Delivery gate: pending
- Owner: test
- Reviewer: test
- Created: 2026-09-04
- Updated: 2026-09-04
- Branch: main
- Base ref / merge-base: {repo.head}
- Source commit: {repo.head}
- Affected paths: docs/**
- Architecture impact: documentation-only
- Data classification: internal
- Provenance: project-authored
- Executable: false
"""


class WorkflowModeTests(unittest.TestCase):
    def test_policy_cannot_remove_a_mode_baseline_field(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            policy["tasks"]["modes"]["LIGHT"]["required_metadata"].remove(
                "Validation"
            )
            repo.write_policy(policy)
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(64, result.returncode, result.stdout + result.stderr)
            self.assertIn("is missing baseline fields: Validation", result.stderr)

    def test_version_1_policy_and_standard_like_record_remain_valid(self) -> None:
        with ValidatorRepo("main") as repo:
            policy = repo.policy()
            for key in ("default_mode", "modes", "metadata_defaults"):
                policy["tasks"].pop(key)
            policy["tasks"]["architecture_impacts"] = [
                "none",
                "documentation-only",
                "architecture-change",
            ]
            gate = policy["architecture_gate"]
            gate["sensitive_globs"] = gate.pop("default_sensitive_globs")
            for key in (
                "enabled",
                "base_ref",
                "additional_sensitive_globs",
                "ignored_globs",
                "mode_escalation",
            ):
                gate.pop(key)
            policy.pop("secret_scan")
            repo.write_policy(policy)
            repo.write(
                ".agents/tasks/active/20260904-legacy.md",
                legacy_task(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_valid_light_task_passes_without_heavy_metadata(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/tasks/active/20260904-light.md", light_task())
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_light_defaults_apply_to_blank_optional_trust_fields(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-light.md",
                light_task()
                + "- Data classification:\n- Provenance:\n- Executable:\n",
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_light_task_missing_required_field_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-light.md",
                light_task().replace("- Validation: unit test pending\n", ""),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("missing metadata: Validation", result.stdout)

    def test_valid_standard_task_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_valid_strict_task_passes(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-strict.md",
                strict_task(repo),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_invalid_mode_fails(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-light.md",
                light_task().replace("Mode: LIGHT", "Mode: FAST"),
            )
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [task-mode]", result.stdout)

    def test_light_task_must_promote_for_architecture_sensitive_change(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-light.md",
                light_task(affected="config/**"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [task-mode-escalation]", result.stdout)
            self.assertIn("promoted to STANDARD", result.stdout)

    def test_security_sensitive_change_requires_strict(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo, affected="src/security/**"),
            )
            repo.write("src/security/policy.py", "ENABLED = True\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("promoted to STRICT", result.stdout)

    def test_standard_task_can_cover_non_strict_sensitive_change(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-standard.md",
                standard_task(repo, affected="config/**"),
            )
            repo.write("config/application.yml", "enabled: true\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("covered by changed no-impact task records", result.stdout)

    def test_strict_task_can_cover_security_sensitive_change(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(
                ".agents/tasks/active/20260904-strict.md",
                strict_task(repo),
            )
            repo.write("security/policy.py", "ENABLED = True\n")
            result = repo.run_validator("--today", "2026-09-04")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("covered by changed no-impact task records", result.stdout)


if __name__ == "__main__":
    unittest.main()
