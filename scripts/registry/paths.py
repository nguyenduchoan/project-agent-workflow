"""Repository path and segment-aware glob helpers."""

from __future__ import annotations

import re
import posixpath
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterable

from registry.findings import ConfigurationError


def normalize_repo_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _segment_regex(segment: str) -> str:
    out = []
    i = 0
    while i < len(segment):
        char = segment[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def repo_glob_match(pattern: str, path: str) -> bool:
    """Match repository paths: * and ? stay within a segment; ** is recursive."""
    pattern = normalize_repo_path(pattern)
    path = normalize_repo_path(path)
    if not pattern:
        return not path
    parts = pattern.split("/")
    path_parts = path.split("/") if path else []

    @lru_cache(maxsize=None)
    def match(pi: int, si: int) -> bool:
        if pi == len(parts):
            return si == len(path_parts)
        part = parts[pi]
        if part == "**":
            return match(pi + 1, si) or (si < len(path_parts) and match(pi, si + 1))
        return si < len(path_parts) and re.fullmatch(_segment_regex(part), path_parts[si]) is not None and match(pi + 1, si + 1)

    return match(0, 0)


def clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "`":
        value = value[1:-1].strip()
    return value


def normalize_repo_relative_glob(raw: object, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError(f"{field_name} entries must be non-empty strings")
    value = raw.strip().replace("\\", "/")
    if "\0" in value:
        raise ConfigurationError(f"{field_name} contains a NUL byte")
    if value.startswith("/") or value.startswith("//") or re.match(r"^[A-Za-z]:", value):
        raise ConfigurationError(f"{field_name} must be repository-relative: {raw!r}")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise ConfigurationError(f"{field_name} must not contain traversal: {raw!r}")
    normalized = posixpath.normpath(value)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ConfigurationError(
            f"{field_name} must identify a path inside the repository: {raw!r}"
        )
    return normalized


def normalize_glob_list(raw: object, field_name: str) -> list[str]:
    if not isinstance(raw, list):
        raise ConfigurationError(f"{field_name} must be an array")
    return [normalize_repo_relative_glob(item, field_name) for item in raw]


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(repo_glob_match(pattern, path) for pattern in patterns)


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def has_symlink_component(root: Path, path: Path) -> bool:
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def data_files(agents_root: Path) -> list[Path]:
    result: list[Path] = []
    for directory in ("tasks", "architecture", "reviews", "policies"):
        root = agents_root / directory
        if root.is_dir() and not root.is_symlink():
            result.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and not has_symlink_component(agents_root, path)
            )
    return sorted(set(result))
