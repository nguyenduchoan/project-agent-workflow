"""Local-only Git operations used by registry validation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from registry.findings import ConfigurationError
from registry.paths import clean


def run_git(
    repo: Path,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
    binary: bool = False,
) -> str | bytes:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        env=environment,
        text=not binary,
    )
    if result.returncode != 0 and not allow_failure:
        stderr = result.stderr
        stdout = result.stdout
        if binary:
            stderr = stderr.decode("utf-8", errors="replace")
            stdout = stdout.decode("utf-8", errors="replace")
        detail = str(stderr).strip() or str(stdout).strip() or "unknown Git failure"
        raise ConfigurationError(
            f"git -C {repo} {' '.join(arguments)} failed ({result.returncode}): {detail}"
        )
    return result.stdout


def resolve_git_root(candidate: Path) -> Path:
    output = run_git(candidate, ["rev-parse", "--show-toplevel"])
    assert isinstance(output, str)
    root = Path(output.strip()).resolve()
    if not root.is_dir():
        raise ConfigurationError(f"resolved Git root is not a directory: {root}")
    return root


def resolve_validation_root(candidate: Path) -> tuple[Path, Path | None]:
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise ConfigurationError(f"project path is not a directory: {resolved}")
    output = run_git(resolved, ["rev-parse", "--show-toplevel"], allow_failure=True)
    assert isinstance(output, str)
    if not output.strip():
        return resolved, None
    root = Path(output.strip()).resolve()
    if not root.is_dir():
        raise ConfigurationError(f"resolved Git root is not a directory: {root}")
    return root, root


def validate_git_ref_syntax(raw: str, label: str) -> str:
    value = clean(raw)
    if not value or value.startswith("-") or any(char.isspace() for char in value):
        raise ConfigurationError(f"unsafe {label}: {raw!r}")
    return value


def git_commit(project_root: Path, ref: str) -> str | None:
    output = run_git(
        project_root,
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        allow_failure=True,
    )
    assert isinstance(output, str)
    return output.strip() or None


def git_merge_base(project_root: Path, left: str, right: str) -> str | None:
    output = run_git(project_root, ["merge-base", left, right], allow_failure=True)
    assert isinstance(output, str)
    return output.strip() or None


def resolve_git_base(
    project_root: Path,
    *,
    explicit_base_ref: str | None,
    configured_base_ref: object = None,
) -> tuple[str | None, str | None]:
    if explicit_base_ref is not None:
        value = validate_git_ref_syntax(explicit_base_ref, "base ref")
        if not git_commit(project_root, value):
            raise ConfigurationError(f"explicit base ref does not resolve to a commit: {value}")
        if not git_merge_base(project_root, "HEAD", value):
            raise ConfigurationError(
                f"explicit base ref does not share history with HEAD: {value}"
            )
        return value, "explicit --base-ref"

    if configured_base_ref is not None:
        if not isinstance(configured_base_ref, str):
            raise ConfigurationError("architecture_gate.base_ref must be a string or null")
        value = validate_git_ref_syntax(configured_base_ref, "configured base ref")
        if not git_commit(project_root, value):
            raise ConfigurationError(f"configured base ref does not resolve to a commit: {value}")
        if not git_merge_base(project_root, "HEAD", value):
            raise ConfigurationError(
                f"configured base ref does not share history with HEAD: {value}"
            )
        return value, "registry policy architecture_gate.base_ref"

    upstream_output = run_git(
        project_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        allow_failure=True,
    )
    assert isinstance(upstream_output, str)
    upstream = upstream_output.strip()
    if upstream:
        merge_base = git_merge_base(project_root, "HEAD", upstream)
        if merge_base and git_commit(project_root, merge_base):
            return merge_base, f"upstream tracking branch merge-base ({upstream})"

    for candidate in ("origin/main", "origin/master", "main", "master"):
        if git_commit(project_root, candidate) and git_merge_base(project_root, "HEAD", candidate):
            return candidate, candidate
    return None, None


def current_git_branch(project_root: Path) -> str:
    output = run_git(project_root, ["branch", "--show-current"])
    assert isinstance(output, str)
    return output.strip()


def current_git_head(project_root: Path) -> str:
    output = run_git(project_root, ["rev-parse", "HEAD"])
    assert isinstance(output, str)
    return output.strip()


def parse_name_status(raw: bytes) -> dict[str, set[str]]:
    tokens = [token.decode("utf-8", errors="replace") for token in raw.split(b"\0") if token]
    result: dict[str, set[str]] = {}
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        code = status[0]
        path_count = 2 if code in {"R", "C"} else 1
        if index + path_count > len(tokens):
            raise ConfigurationError("malformed Git name-status output")
        for path in tokens[index : index + path_count]:
            result.setdefault(path, set()).add(code)
        index += path_count
    return result


def merge_changes(target: dict[str, set[str]], source: dict[str, set[str]]) -> None:
    for path, statuses in source.items():
        target.setdefault(path, set()).update(statuses)


def changed_entries(project_root: Path, base_ref: str) -> dict[str, set[str]]:
    base_ref = validate_git_ref_syntax(base_ref, "base ref")
    run_git(project_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    result: dict[str, set[str]] = {}
    for arguments in (
        ["diff", "--no-ext-diff", "--name-status", "-z", f"{base_ref}...HEAD", "--"],
        ["diff", "--no-ext-diff", "--name-status", "-z", "--"],
        ["diff", "--no-ext-diff", "--cached", "--name-status", "-z", "--"],
    ):
        output = run_git(project_root, arguments, binary=True)
        assert isinstance(output, bytes)
        merge_changes(result, parse_name_status(output))
    untracked = run_git(
        project_root,
        ["ls-files", "-z", "--others", "--exclude-standard", "--"],
        binary=True,
    )
    assert isinstance(untracked, bytes)
    for raw_path in untracked.split(b"\0"):
        if raw_path:
            result.setdefault(raw_path.decode("utf-8", errors="replace"), set()).add("A")
    return result
