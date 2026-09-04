#!/usr/bin/env python3
"""Assemble the manifest-locked runtime artifact for official validation."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PACKAGE_ROOT / "skill-manifest.txt"
SKILL_NAME = "project-agent-workflow"


def manifest_entries() -> list[Path]:
    entries: list[Path] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        entry = Path(value)
        if entry.is_absolute() or value.startswith("./") or ".." in entry.parts:
            raise ValueError(f"unsafe runtime manifest entry: {value}")
        entries.append(entry)
    if len(entries) != len(set(entries)):
        raise ValueError("runtime manifest contains duplicate entries")
    return entries


def assemble(destination: Path) -> None:
    destination = destination.resolve()
    if destination.name != SKILL_NAME:
        raise ValueError(f"destination directory must be named {SKILL_NAME}")
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    destination.mkdir(parents=True)
    for entry in manifest_entries():
        source = PACKAGE_ROOT / entry
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"manifest source is missing or a symlink: {entry.as_posix()}")
        target = destination / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        assemble(args.destination)
    except (OSError, ValueError) as exc:
        print(f"artifact assembly: ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Assembled Agent Skill artifact: {args.destination.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
