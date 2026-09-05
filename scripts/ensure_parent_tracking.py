#!/usr/bin/env python3
"""Maintain the root .gitignore rule that makes the whole .agents tree trackable."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

from registry.hosts import HOSTS, HostDefinition, expand_host_ids, validate_registry
from registry.findings import ConfigurationError


BEGIN = "# project-agent-workflow: begin"
END = "# project-agent-workflow: end"


class TrackingError(ValueError):
    pass


@dataclass(frozen=True)
class TrackingPlan:
    content: str
    action: str
    mode: int
    expected_content: bytes | None


def managed_block_lines(hosts: tuple[HostDefinition, ...]) -> tuple[str, ...]:
    validate_registry()
    lines = [BEGIN, "!/.agents/", "!/.agents/**"]
    for host in hosts:
        if host.owned_root == ".agents":
            continue
        components = Path(host.destination).parts
        for index in range(1, len(components) + 1):
            current = "/".join(components[:index])
            lines.append(f"!/{current}/")
            if index < len(components):
                lines.append(f"/{current}/*")
        lines.append(f"!/{host.destination}/**")
    lines.append(END)
    return tuple(lines)


def relevant_hosts(root: Path, selected_ids: list[str]) -> tuple[HostDefinition, ...]:
    validate_registry()
    selected = {host.id for host in expand_host_ids(selected_ids)} if selected_ids else set()
    return tuple(
        host
        for host in HOSTS
        if host.id in selected or recognized_installation(root, host)
    )


def recognized_installation(root: Path, host: HostDefinition) -> bool:
    """Recognize package data using this helper's verifier, never host code."""
    skill_root = root
    try:
        for component in Path(host.destination).parts:
            skill_root /= component
            if skill_root.is_symlink():
                return False
        if not skill_root.is_dir():
            return False
        manifest_path = skill_root / "skill-manifest.txt"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            return False
        if any(path.is_symlink() for path in skill_root.rglob("*")):
            return False
        verifier = Path(__file__).resolve().with_name("verify_install.py")
        if verifier.is_symlink() or not verifier.is_file():
            raise TrackingError("package verifier is missing or is a symlink")
        from verify_install import EXPECTED_SKILL_FILES, verify_runtime_manifest

        if any(not (skill_root / entry).is_file() for entry in EXPECTED_SKILL_FILES):
            return False
        errors: list[str] = []
        verify_runtime_manifest(skill_root, errors)
        if errors:
            return False
        entrypoint = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        return (
            entrypoint.startswith("---\n")
            and re.search(r"(?m)^name:\s*project-agent-workflow\s*$", entrypoint) is not None
            and re.search(r"(?m)^description:\s*\S", entrypoint) is not None
        )
    except (OSError, UnicodeError):
        return False


def git_root(candidate: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TrackingError(f"not inside a Git repository: {candidate}")
    root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        raise TrackingError(f"resolved Git root is not a directory: {root}")
    return root


def current_content(path: Path) -> bytes | None:
    if path.is_symlink():
        raise TrackingError(f"refusing symlink .gitignore: {path}")
    if path.exists() and not path.is_file():
        raise TrackingError(f".gitignore is not a regular file: {path}")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def desired_content(path: Path, hosts: tuple[HostDefinition, ...]) -> TrackingPlan:
    expected_content = current_content(path)
    try:
        original = expected_content.decode("utf-8") if expected_content is not None else ""
    except UnicodeDecodeError as exc:
        raise TrackingError(f".gitignore must be UTF-8 text: {path}") from exc

    begin_count = sum(line == BEGIN for line in original.splitlines())
    end_count = sum(line == END for line in original.splitlines())
    if (begin_count, end_count) not in {(0, 0), (1, 1)}:
        raise TrackingError("managed .gitignore markers are duplicated or incomplete")

    newline = "\r\n" if "\r\n" in original and "\n" not in original.replace("\r\n", "") else "\n"
    remaining = original
    action = "append"
    block_lines = managed_block_lines(hosts)
    if begin_count == 1:
        block_pattern = re.compile(
            rf"(?ms)^{re.escape(BEGIN)}\r?\n.*?^{re.escape(END)}(?:\r?\n|$)"
        )
        matches = list(block_pattern.finditer(original))
        if len(matches) != 1:
            raise TrackingError("managed .gitignore block was edited; reconcile it manually")
        match = matches[0]
        existing_lines = tuple(match.group(0).rstrip("\r\n").splitlines())
        preserved_hosts = tuple(
            host for host in HOSTS if f"!/{host.destination}/**" in existing_lines
        )
        if existing_lines != managed_block_lines(preserved_hosts):
            raise TrackingError("managed .gitignore block was edited; reconcile it manually")
        block_lines = managed_block_lines(tuple(
            host for host in HOSTS if host in hosts or host in preserved_hosts
        ))
        remaining = original[: match.start()] + original[match.end() :]
        action = "move-to-end"

    prefix = remaining.rstrip("\r\n")
    block = newline.join(block_lines) + newline
    desired = f"{prefix}{newline if prefix else ''}{block}"
    if desired == original:
        action = "unchanged"
    mode = path.stat().st_mode & 0o777 if expected_content is not None else 0o644
    return TrackingPlan(desired, action, mode, expected_content)


def assert_unchanged(path: Path, expected_content: bytes | None) -> None:
    if current_content(path) != expected_content:
        raise TrackingError(f".gitignore changed during installation: {path}")


def write_atomic(path: Path, content: str, mode: int, expected_content: bytes | None) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".project-agent-workflow.gitignore.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        assert_unchanged(path, expected_content)
        if expected_content is None:
            # Exclusive creation also protects a path appearing after the recheck.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_plan(path: Path, hosts: tuple[HostDefinition, ...], plan: TrackingPlan) -> None:
    assert_unchanged(path, plan.expected_content)
    if plan.action == "unchanged":
        print(f"parent tracking: unchanged {path}")
        return
    write_atomic(path, plan.content, plan.mode, plan.expected_content)
    if desired_content(path, hosts).action != "unchanged":
        raise TrackingError(".gitignore update did not reach canonical state")
    print(f"parent tracking: updated {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--host", action="append", default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = git_root(args.project.resolve())
        hosts = relevant_hosts(root, args.host)
        path = root / ".gitignore"
        plan = desired_content(path, hosts)
        if args.apply:
            apply_plan(path, hosts, plan)
        elif plan.action == "unchanged":
            print(f"parent tracking: unchanged {path}")
        elif args.dry_run:
            print(f"parent tracking: would {plan.action} managed block in {path}")
        else:
            print(f"parent tracking: preflight allows {plan.action} in {path}")
    except (ConfigurationError, OSError, TrackingError, ValueError) as exc:
        print(f"parent tracking: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
