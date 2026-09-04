#!/usr/bin/env python3
"""Verify that the source tree is a generic, self-contained skill package."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PACKAGE_ROOT / "skill-manifest.txt"
RUNTIME_ROOT_FILES = {"LICENSE", "SKILL.md", "VERSION", "install.sh", "skill-manifest.txt"}
RUNTIME_DIRECTORIES = ("agents", "assets", "references", "scripts")
REQUIRED_RELEASE_FILES = (
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SKILL.md",
    "VERSION",
    "agents/openai.yaml",
    "install.sh",
    "skill-manifest.txt",
    "tests/assemble_skill.py",
    "tests/test_install.sh",
    "tests/test_package.py",
    "tests/test_validator_architecture_gate.py",
    "tests/test_validator_git_metadata.py",
    "tests/test_validator_markdown.py",
    "tests/test_validator_modes.py",
    "tests/test_validator_policy.py",
    "tests/test_validator_secrets.py",
    "tests/test_validator_staleness.py",
    "tests/validator_test_support.py",
    "tests/verify_package.py",
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
TEXT_SUFFIXES = {".json", ".md", ".py", ".sh", ".txt", ".yaml", ".yml"}


def relative(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def manifest_entries(errors: list[str]) -> list[str]:
    if not MANIFEST_PATH.is_file() or MANIFEST_PATH.is_symlink():
        errors.append("skill-manifest.txt must be a regular, non-symlink file")
        return []
    entries = [
        line.strip()
        for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(entries) != len(set(entries)):
        errors.append("skill-manifest.txt contains duplicate entries")
    if entries != sorted(entries):
        errors.append("skill-manifest.txt entries must be sorted")
    for entry in entries:
        candidate = Path(entry)
        if candidate.is_absolute() or entry.startswith("./") or ".." in candidate.parts:
            errors.append(f"unsafe runtime manifest entry: {entry}")
    return entries


def runtime_files(errors: list[str]) -> set[str]:
    files = set(RUNTIME_ROOT_FILES)
    for directory_name in RUNTIME_DIRECTORIES:
        directory = PACKAGE_ROOT / directory_name
        if not directory.is_dir() or directory.is_symlink():
            errors.append(f"runtime directory is missing or is a symlink: {directory_name}")
            continue
        for path in directory.rglob("*"):
            if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                errors.append(f"runtime path is a symlink: {relative(path)}")
            elif path.is_file():
                files.add(relative(path))
    return files


def package_text_files() -> list[Path]:
    result: list[Path] = []
    for path in PACKAGE_ROOT.rglob("*"):
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"LICENSE", "VERSION"}:
            result.append(path)
    return sorted(result)


def verify_skill_metadata(errors: list[str]) -> None:
    skill_path = PACKAGE_ROOT / "SKILL.md"
    if not skill_path.is_file():
        return
    text = skill_path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        errors.append("SKILL.md must start with YAML frontmatter")
        return
    frontmatter = text.split("\n---\n", 1)[0][4:]
    if not re.search(r"(?m)^name:\s*project-agent-workflow\s*$", frontmatter):
        errors.append("SKILL.md must declare name: project-agent-workflow")
    description = re.search(r"(?m)^description:\s*(\S.*)$", frontmatter)
    if description is None:
        errors.append("SKILL.md must have a single-line description")
    elif len(description.group(1)) > 1024:
        errors.append("SKILL.md description exceeds 1024 characters")
    compatibility = re.search(r"(?m)^compatibility:\s*(\S.*)$", frontmatter)
    if compatibility is None:
        errors.append("SKILL.md must declare runtime compatibility")
    elif len(compatibility.group(1)) > 500:
        errors.append("SKILL.md compatibility exceeds 500 characters")

    metadata_path = PACKAGE_ROOT / "agents" / "openai.yaml"
    if metadata_path.is_file():
        metadata = metadata_path.read_text(encoding="utf-8")
        if "$project-agent-workflow" not in metadata:
            errors.append("agents/openai.yaml default prompt must mention the skill")
        if not re.search(r"(?m)^\s*allow_implicit_invocation:\s*false\s*$", metadata):
            errors.append("agents/openai.yaml must disable implicit invocation")

    version_path = PACKAGE_ROOT / "VERSION"
    if version_path.is_file():
        version = version_path.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"0|[1-9]\d*\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
            errors.append(f"VERSION is not semantic versioning compatible: {version}")

    policy_path = (
        PACKAGE_ROOT
        / "assets"
        / "project-template"
        / ".agents"
        / "policies"
        / "registry-policy.json"
    )
    if policy_path.is_file():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid registry policy JSON: {exc}")
        else:
            if policy.get("schema_version") != 1:
                errors.append("registry policy schema_version must be 1")


def main() -> int:
    errors: list[str] = []
    entries = manifest_entries(errors)
    expected_runtime = runtime_files(errors)

    for path in PACKAGE_ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            errors.append(f"source package path is a symlink: {relative(path)}")

    for required in REQUIRED_RELEASE_FILES:
        path = PACKAGE_ROOT / required
        if path.is_symlink() or not path.is_file():
            errors.append(f"missing regular release file: {required}")

    entry_set = set(entries)
    for missing in sorted(expected_runtime - entry_set):
        errors.append(f"runtime file is absent from skill-manifest.txt: {missing}")
    for extra in sorted(entry_set - expected_runtime):
        errors.append(f"manifest entry is not a runtime file: {extra}")
    for entry in entries:
        path = PACKAGE_ROOT / entry
        if path.is_symlink() or not path.is_file():
            errors.append(f"manifest entry is missing or is a symlink: {entry}")
        elif path.stat().st_size == 0 and path.name != ".gitkeep":
            errors.append(f"manifest entry is empty: {entry}")

    for path in package_text_files():
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in PROJECT_SPECIFIC_TOKENS:
            if token in lowered:
                errors.append(f"project-specific token '{token}' found in {relative(path)}")
        for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"secret-like content found in {relative(path)}")

    verify_skill_metadata(errors)

    executable_paths = [PACKAGE_ROOT / "install.sh"]
    executable_paths.extend((PACKAGE_ROOT / "scripts").glob("*.sh"))
    executable_paths.extend((PACKAGE_ROOT / "scripts").glob("*.py"))
    executable_paths.extend((PACKAGE_ROOT / "tests").glob("*.sh"))
    executable_paths.extend((PACKAGE_ROOT / "tests").glob("*.py"))
    for path in sorted(set(executable_paths)):
        if path.is_file() and not os.access(path, os.X_OK):
            errors.append(f"script is not executable: {relative(path)}")

    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}")
        print(f"Package verification: {len(set(errors))} error(s).")
        return 1

    print(
        "Package verification: 0 error(s); source is generic, complete, and manifest-locked."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
