#!/usr/bin/env python3
"""Verify the observable invariants of a project-agent-workflow installation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from registry.findings import ConfigurationError
from registry.hosts import HOSTS, expand_host_ids, validate_registry
from registry.preferences import load_preferences


EXPECTED_PROJECT_FILES = (
    ".agents/README.md",
    ".agents/policies/context-budget.md",
    ".agents/policies/registry-policy.json",
    ".agents/policies/trust-and-data.md",
    ".agents/tasks/templates/task.md",
    ".agents/architecture/manifest.yml",
    ".agents/architecture/flows.md",
    ".agents/architecture/changes/index.md",
    ".agents/architecture/templates/branch-history.md",
    ".agents/architecture/templates/architecture-change.md",
    ".agents/reviews/templates/review-report.md",
)

EXPECTED_SKILL_FILES = (
    "SKILL.md",
    "VERSION",
    "LICENSE",
    "skill-manifest.txt",
    "install.sh",
    "agents/openai.yaml",
    "references/installation.md",
    "references/registry-workflow.md",
    "scripts/ensure_parent_tracking.py",
    "scripts/install.py",
    "scripts/install.sh",
    "scripts/registry/__init__.py",
    "scripts/registry/architecture.py",
    "scripts/registry/findings.py",
    "scripts/registry/git.py",
    "scripts/registry/hosts.py",
    "scripts/registry/paths.py",
    "scripts/registry/policy.py",
    "scripts/registry/preferences.py",
    "scripts/registry/records.py",
    "scripts/registry/secrets.py",
    "scripts/validate_registry.py",
    "scripts/verify_install.py",
)

PROJECT_SPECIFIC_TOKENS = (
    "pay" + "gate",
    "tech" + "lab",
    "mb" + "bank",
    "bv" + "b",
)

HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b"),
)


def git_root(candidate: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"not inside a Git repository: {candidate}")
    return Path(result.stdout.strip()).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--host", action="append", default=[])
    scope.add_argument("--all-installed-hosts", action="store_true")
    return parser.parse_args()


def verify_runtime_manifest(skill_root: Path, errors: list[str]) -> None:
    manifest_path = skill_root / "skill-manifest.txt"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        errors.append("installed skill is missing a regular skill-manifest.txt")
        return
    try:
        entries = [
            line.strip()
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except UnicodeDecodeError as exc:
        errors.append(f"installed skill manifest is not UTF-8: {exc}")
        return
    if len(entries) != len(set(entries)):
        errors.append("installed skill manifest contains duplicate entries")
    if entries != sorted(entries):
        errors.append("installed skill manifest entries are not sorted")

    entry_set = set(entries)
    for entry in entries:
        relative_path = Path(entry)
        if relative_path.is_absolute() or entry.startswith("./") or ".." in relative_path.parts:
            errors.append(f"unsafe installed manifest entry: {entry}")
            continue
        path = skill_root / relative_path
        if path.is_symlink() or not path.is_file():
            errors.append(f"installed manifest entry is missing or is a symlink: {entry}")
        elif path.stat().st_size == 0 and path.name != ".gitkeep":
            errors.append(f"installed manifest entry is empty: {entry}")

    actual_files: set[str] = set()
    for path in skill_root.rglob("*"):
        rel_path = path.relative_to(skill_root).as_posix()
        if path.is_symlink():
            errors.append(f"installed skill path is a symlink: {rel_path}")
        elif path.is_file():
            actual_files.add(rel_path)
    for missing in sorted(entry_set - actual_files):
        errors.append(f"installed runtime file is missing: {missing}")
    for unexpected in sorted(actual_files - entry_set):
        errors.append(f"installed skill contains a non-manifest file: {unexpected}")


def main() -> int:
    args = parse_args()
    try:
        root = git_root(args.project.resolve())
    except ValueError as exc:
        print(f"install verification: {exc}", file=sys.stderr)
        return 64

    agents_root = root / ".agents"
    errors: list[str] = []
    try:
        validate_registry()
        scoped_hosts = expand_host_ids(args.host) if args.host else list(HOSTS)
    except (ConfigurationError, ValueError) as exc:
        errors.append(f"invalid trusted host selection: {exc}")
        scoped_hosts = []
    if args.host:
        verification_hosts = [
            (host, root / host.destination)
            for host in scoped_hosts
        ]
    else:
        verification_hosts = [
            (host, root / host.destination)
            for host in scoped_hosts
            if (root / host.destination).exists()
            or (root / host.destination).is_symlink()
        ]
    if not verification_hosts:
        errors.append("no registered host package is selected or installed")

    expected = [root / item for item in EXPECTED_PROJECT_FILES]
    for _host, skill_root in verification_hosts:
        expected.extend(skill_root / item for item in EXPECTED_SKILL_FILES)

    if agents_root.is_symlink():
        errors.append("managed .agents root is a symlink")
    for host, skill_root in verification_hosts:
        if skill_root.is_symlink():
            errors.append(f"installed {host.id} skill root is a symlink")
        elif not skill_root.is_dir():
            errors.append(f"installed {host.id} skill root is not a directory")
        else:
            verify_runtime_manifest(skill_root, errors)

    if verification_hosts:
        tracking_helper = Path(__file__).resolve().parent / "ensure_parent_tracking.py"
        if tracking_helper.is_file():
            tracking_command = [
                sys.executable,
                str(tracking_helper),
                "--project",
                str(root),
            ]
            for host, _skill_root in verification_hosts:
                tracking_command.extend(["--host", host.id])
            tracking = subprocess.run(
                [*tracking_command, "--check"],
                check=False,
                capture_output=True,
                text=True,
            )
            if tracking.returncode != 0 or "unchanged" not in tracking.stdout:
                detail = tracking.stderr.strip() or tracking.stdout.strip()
                errors.append(f"parent tracking block is not canonical: {detail}")

    for path in expected:
        if path.is_symlink():
            errors.append(f"managed path is a symlink: {path.relative_to(root)}")
        elif not path.is_file():
            errors.append(f"missing managed file: {path.relative_to(root)}")
        elif path.stat().st_size == 0:
            errors.append(f"managed file is empty: {path.relative_to(root)}")

    for _host, skill_root in verification_hosts:
        skill_entrypoint = skill_root / "SKILL.md"
        if skill_entrypoint.is_file():
            text = skill_entrypoint.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                errors.append("SKILL.md is missing YAML frontmatter")
            if not re.search(r"(?m)^name:\s*project-agent-workflow\s*$", text):
                errors.append("SKILL.md has an unexpected skill name")
            if not re.search(r"(?m)^description:\s*\S", text):
                errors.append("SKILL.md is missing a non-empty description")
        metadata_path = skill_root / "agents" / "openai.yaml"
        if metadata_path.is_file() and "$project-agent-workflow" not in metadata_path.read_text(encoding="utf-8"):
            errors.append("agents/openai.yaml default prompt does not mention the skill")

    policy_path = root / ".agents" / "policies" / "registry-policy.json"
    if policy_path.is_file():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid registry policy JSON: {exc}")
        else:
            if policy.get("schema_version") != 1:
                errors.append("registry policy schema_version must be 1")

    preferences_path = agents_root / "preferences.json"
    if preferences_path.exists() or preferences_path.is_symlink():
        try:
            load_preferences(preferences_path)
        except ValueError as exc:
            errors.append(f"invalid shared preferences: {exc}")

    managed_text_files = []
    for _host, skill_root in verification_hosts:
        managed_text_files.extend(
            path
            for path in skill_root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".py", ".sh"}
        )
    managed_text_files.extend(
        path
        for path in expected
        if path.is_file()
        and path.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".py", ".sh"}
    )
    for path in managed_text_files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in PROJECT_SPECIFIC_TOKENS:
            if token in lowered:
                errors.append(
                    f"project-specific token '{token}' leaked into {path.relative_to(root)}"
                )
        for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"secret-like content found in {path.relative_to(root)}")

    executable_files = (
        "install.sh",
        "scripts/ensure_parent_tracking.py",
        "scripts/install.py",
        "scripts/install.sh",
        "scripts/validate_registry.py",
        "scripts/verify_install.py",
    )
    for _host, skill_root in verification_hosts:
        for path in skill_root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in {".py", ".sh"}
                and path.stat().st_mode & 0o022
            ):
                errors.append(
                    f"installed runtime code is group/world-writable: {path.relative_to(root)}"
                )
        for relative_path in executable_files:
            path = skill_root / relative_path
            if path.is_file() and not os.access(path, os.X_OK):
                errors.append(f"installed script is not executable: {path.relative_to(root)}")

    managed_roots = [
        agents_root,
        *(
            skill_root
            for host, skill_root in verification_hosts
            if host.owned_root != ".agents"
        ),
    ]
    for managed_root in managed_roots:
        if managed_root.is_dir() and not managed_root.is_symlink():
            for path in sorted(item for item in managed_root.rglob("*") if item.is_file()):
                rel_path = path.relative_to(root).as_posix()
                ignored = subprocess.run(
                    [
                        "git", "-C", str(root), "check-ignore", "--no-index",
                        "-q", "--", rel_path,
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if ignored.returncode == 0:
                    errors.append(f"parent Git still ignores managed file: {rel_path}")
                elif ignored.returncode != 1:
                    errors.append(
                        f"could not evaluate Git ignore state for {rel_path}: {ignored.stderr.strip()}"
                    )

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"Install verification: {len(errors)} error(s).")
        return 1

    print(
        "Install verification: 0 error(s); repo-scoped skill and generic workflow are complete."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
