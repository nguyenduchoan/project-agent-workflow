#!/usr/bin/env python3
"""Maintain the root .gitignore rule that makes the whole .agents tree trackable."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

from registry.hosts import HOSTS, validate_registry
from registry.findings import ConfigurationError


BEGIN = "# project-agent-workflow: begin"
END = "# project-agent-workflow: end"
LEGACY_BLOCK_LINES = (BEGIN, "!/.agents/", "!/.agents/**", END)


class TrackingError(RuntimeError):
    pass


def managed_block_lines() -> tuple[str, ...]:
    validate_registry()
    lines = [BEGIN, "!/.agents/", "!/.agents/**"]
    for host in HOSTS:
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


def desired_content(path: Path) -> tuple[str, str, int]:
    if path.is_symlink():
        raise TrackingError(f"refusing symlink .gitignore: {path}")
    if path.exists() and not path.is_file():
        raise TrackingError(f".gitignore is not a regular file: {path}")

    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
    except UnicodeDecodeError as exc:
        raise TrackingError(f".gitignore must be UTF-8 text: {path}") from exc

    begin_count = sum(line == BEGIN for line in original.splitlines())
    end_count = sum(line == END for line in original.splitlines())
    if (begin_count, end_count) not in {(0, 0), (1, 1)}:
        raise TrackingError("managed .gitignore markers are duplicated or incomplete")

    newline = "\r\n" if "\r\n" in original and "\n" not in original.replace("\r\n", "") else "\n"
    remaining = original
    action = "append"
    block_lines = managed_block_lines()
    if begin_count == 1:
        block_pattern = re.compile(
            rf"(?ms)^{re.escape(BEGIN)}\r?\n.*?^{re.escape(END)}(?:\r?\n|$)"
        )
        matches = list(block_pattern.finditer(original))
        if len(matches) != 1:
            raise TrackingError("managed .gitignore block was edited; reconcile it manually")
        match = matches[0]
        existing_lines = tuple(match.group(0).rstrip("\r\n").splitlines())
        if existing_lines not in {LEGACY_BLOCK_LINES, block_lines}:
            raise TrackingError("managed .gitignore block was edited; reconcile it manually")
        remaining = original[: match.start()] + original[match.end() :]
        action = "move-to-end"

    prefix = remaining.rstrip("\r\n")
    block = newline.join(block_lines) + newline
    desired = f"{prefix}{newline if prefix else ''}{block}"
    if desired == original:
        action = "unchanged"
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    return desired, action, mode


def write_atomic(path: Path, content: str, mode: int) -> None:
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
        if path.is_symlink():
            raise TrackingError(f".gitignore became a symlink during update: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = git_root(args.project.resolve())
        path = root / ".gitignore"
        desired, action, mode = desired_content(path)
        if args.apply and action != "unchanged":
            write_atomic(path, desired, mode)
            _, verified_action, _ = desired_content(path)
            if verified_action != "unchanged":
                raise TrackingError(".gitignore update did not reach canonical state")
            print(f"parent tracking: updated {path}")
        elif action == "unchanged":
            print(f"parent tracking: unchanged {path}")
        elif args.dry_run:
            print(f"parent tracking: would {action} managed block in {path}")
        else:
            print(f"parent tracking: preflight allows {action} in {path}")
    except (ConfigurationError, OSError, TrackingError) as exc:
        print(f"parent tracking: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
