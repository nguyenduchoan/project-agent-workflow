#!/usr/bin/env python3
"""Validate a parent-tracked, project-local .agents registry."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Sequence

sys.dont_write_bytecode = True

from registry.architecture import (  # noqa: E402
    scan_architecture_gate,
    scan_records,
    validate_architecture_relationships,
)
from registry.findings import ConfigurationError, Finding  # noqa: E402
from registry.git import current_git_branch, resolve_git_base, resolve_validation_root  # noqa: E402
from registry.paths import data_files, has_symlink_component  # noqa: E402
from registry.policy import load_policy  # noqa: E402
from registry.preferences import load_preferences  # noqa: E402
from registry.records import scan_context_budget, scan_links, scan_symlinks, scan_tasks  # noqa: E402
from registry.secrets import scan_trust_and_secrets  # noqa: E402


EXIT_VALIDATION = 1
EXIT_CONFIGURATION = 64


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    architecture_group = parser.add_mutually_exclusive_group()
    architecture_group.add_argument("--base-ref")
    architecture_group.add_argument("--no-architecture-gate", action="store_true")
    parser.add_argument("--today", help="validation date override in YYYY-MM-DD format")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def validation_date_from_arg(raw: str | None) -> date:
    if raw is None:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigurationError(f"--today must be an ISO date (YYYY-MM-DD): {raw}") from exc


def validate_project(args: argparse.Namespace) -> tuple[list[Finding], list[str], list[str]]:
    validation_date = validation_date_from_arg(args.today)
    project_root, git_root = resolve_validation_root(args.project)
    agents_root = project_root / ".agents"
    if not agents_root.is_dir():
        raise ConfigurationError(f"missing project registry: {agents_root}")
    if agents_root.is_symlink():
        raise ConfigurationError(f"project registry must not be a symlink: {agents_root}")
    if (agents_root / ".git").exists():
        raise ConfigurationError(
            "nested .agents Git mode is project-specific; use that project's native validator"
        )
    policy_path = agents_root / "policies" / "registry-policy.json"
    if has_symlink_component(agents_root, policy_path):
        raise ConfigurationError(f"registry policy path must not contain a symlink: {policy_path}")
    policy = load_policy(policy_path)
    files = data_files(agents_root)
    notes: list[str] = []
    warnings: list[str] = []
    findings: list[Finding] = []
    preferences_path = agents_root / "preferences.json"
    if preferences_path.exists() or preferences_path.is_symlink():
        try:
            load_preferences(preferences_path)
        except ValueError as exc:
            findings.append(Finding("preferences", "preferences.json", str(exc)))
    findings.extend(scan_symlinks(agents_root))
    task_findings, task_records = scan_tasks(
        agents_root, git_root, policy, validation_date, warnings
    )
    findings.extend(task_findings)
    record_findings, architecture_records = scan_records(
        agents_root, git_root, policy, validation_date, warnings, task_records
    )
    findings.extend(record_findings)
    findings.extend(
        validate_architecture_relationships(task_records, architecture_records, warnings)
    )
    findings.extend(scan_trust_and_secrets(agents_root, files, policy))
    findings.extend(scan_links(agents_root, project_root, files))
    findings.extend(scan_context_budget(agents_root, files, policy))
    architecture_policy = policy["architecture_gate"]
    if args.no_architecture_gate:
        notes.append("architecture gate disabled by explicit --no-architecture-gate")
    elif not architecture_policy.get("enabled", True):
        notes.append("architecture gate disabled by registry policy")
    elif git_root is None:
        if args.base_ref is not None:
            raise ConfigurationError("--base-ref requires a Git repository")
        warnings.append("architecture gate skipped: --project is not inside a Git repository")
    else:
        configured_base_ref = architecture_policy.get(
            "base_ref", architecture_policy.get("default_base_ref")
        )
        base_ref, base_source = resolve_git_base(
            git_root,
            explicit_base_ref=args.base_ref,
            configured_base_ref=configured_base_ref,
        )
        if base_ref is None:
            warnings.append("architecture gate skipped: no safe base reference could be resolved")
        else:
            notes.append(f"architecture base resolved from {base_source}: {base_ref}")
            if not current_git_branch(git_root):
                warnings.append(
                    "architecture gate skipped: checkout-relative evidence cannot be selected because HEAD is detached"
                )
            else:
                gate_findings, gate_notes = scan_architecture_gate(
                    git_root,
                    agents_root,
                    base_ref,
                    policy,
                    findings,
                    task_records,
                    architecture_records,
                )
                findings.extend(gate_findings)
                notes.extend(gate_notes)
    if git_root is None:
        warnings.append("Git metadata object validation skipped: no Git repository was resolved")
    return sorted(set(findings)), sorted(set(warnings)), notes


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        findings, warnings, notes = validate_project(args)
    except ConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    if args.json_output:
        print(
            json.dumps(
                {
                    "errors": len(findings),
                    "warnings": warnings,
                    "notes": notes,
                    "findings": [asdict(item) for item in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(f"ERROR [{finding.code}] {finding.path}: {finding.message}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for note in notes:
            print(f"INFO: {note}")
        print(f"Registry validation: {len(findings)} error(s).")
    return EXIT_VALIDATION if findings else 0


if __name__ == "__main__":
    sys.exit(main())
