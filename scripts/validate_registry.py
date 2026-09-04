#!/usr/bin/env python3
"""Validate a parent-tracked, project-local .agents registry."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from urllib.parse import unquote, urlparse


EXIT_VALIDATION = 1
EXIT_CONFIGURATION = 64
METADATA_RE = re.compile(r"^\s*-\s+([^:\n]+):\s*(.*?)\s*$")
INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+(?:\s+['\"(].*)?)$", re.MULTILINE)
MODE_ORDER = {"LIGHT": 0, "STANDARD": 1, "STRICT": 2}
REGISTRY_TEXT_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}
DEFAULT_MODE_REQUIREMENTS = {
    "LIGHT": (
        "ID",
        "Status",
        "Created",
        "Updated",
        "Affected paths",
        "Acceptance criteria",
        "Validation",
        "Architecture impact",
    ),
    "STANDARD": (
        "ID",
        "Status",
        "Created",
        "Updated",
        "Affected paths",
        "Acceptance criteria",
        "Validation",
        "Architecture impact",
        "Branch",
        "Base ref",
        "Source commit",
        "Risks",
        "Dependencies",
        "Related architecture records",
        "Review notes",
    ),
    "STRICT": (
        "ID",
        "Status",
        "Created",
        "Updated",
        "Affected paths",
        "Acceptance criteria",
        "Validation",
        "Architecture impact",
        "Branch",
        "Base ref",
        "Source commit",
        "Risks",
        "Dependencies",
        "Related architecture records",
        "Review notes",
        "Owner",
        "Reviewer",
        "Delivery gate",
        "Merge-base",
        "Current head",
        "Rollout",
        "Rollback",
        "Evidence",
        "Data classification",
        "Provenance",
    ),
}
DEFAULT_TASK_METADATA_DEFAULTS = {
    "LIGHT": {
        "Data classification": "internal",
        "Provenance": "project-authored",
        "Executable": "false",
    },
    "STANDARD": {
        "Data classification": "internal",
        "Provenance": "project-authored",
        "Executable": "false",
    },
    "STRICT": {"Executable": "false"},
}
MAX_ADDITIONAL_SECRET_PATTERNS = 32
MAX_SECRET_PATTERN_LENGTH = 512
BUILTIN_SECRET_PATTERNS = (
    ("private-key", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    (
        "bearer-credential",
        r"(?i)\bauthorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    ),
    (
        "jwt-credential",
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b",
    ),
    (
        "credential-in-url",
        r"(?i)\b(?:https?|postgres(?:ql)?|redis)://[^/\s:@]+:[^/\s@]+@",
    ),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    (
        "github-token",
        r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b",
    ),
)
NESTED_QUANTIFIER_RE = re.compile(
    r"\((?:\\.|[^()]){0,256}(?:[+*]|\{\d+(?:,\d*)?\})"
    r"(?:\\.|[^()]){0,256}\)\s*(?:[+*]|\{\d+(?:,\d*)?\})"
)
QUANTIFIED_ALTERNATION_RE = re.compile(
    r"\((?:\\.|[^()]){0,256}\|(?:\\.|[^()]){0,256}\)\s*"
    r"(?:[+*]|\{\d+(?:,\d*)?\})"
)

METADATA_ALIASES = {
    "Status": ("Status", "Trạng thái"),
    "Created": ("Created", "Ngày tạo"),
    "Updated": ("Updated", "Cập nhật gần nhất"),
    "Architecture impact": ("Architecture impact", "Phân loại"),
}


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Finding:
    code: str
    path: str
    message: str


class TaskRelevance(Enum):
    CURRENT = "current-branch"
    OTHER_ACTIVE = "other-active-branch"
    DETACHED = "detached-head"
    HISTORICAL = "historical"


class RecordRelevance(Enum):
    CURRENT = "current-branch"
    LINKED = "linked"
    ACTIVE_OTHER = "other active branch"
    HISTORICAL = "historical"


@dataclass
class TaskRecord:
    path: Path
    rel_path: str
    metadata: dict[str, str]
    relevance: TaskRelevance
    architecture_links: tuple[Path, ...]
    affected_paths: tuple[str, ...]


@dataclass
class ArchitectureRecord:
    path: Path
    rel_path: str
    kind: str
    metadata: dict[str, str]
    relevance: RecordRelevance
    verified_at: date | None
    stale_after_days: int | None
    stale_age_days: int | None
    related_task_path: Path | None
    affected_paths: tuple[str, ...]


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
    """Resolve the validation root and its Git root, when one exists."""
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise ConfigurationError(f"project path is not a directory: {resolved}")
    output = run_git(resolved, ["rev-parse", "--show-toplevel"], allow_failure=True)
    assert isinstance(output, str)
    if not output.strip():
        return resolved, None
    git_root = Path(output.strip()).resolve()
    if not git_root.is_dir():
        raise ConfigurationError(f"resolved Git root is not a directory: {git_root}")
    return git_root, git_root


def validate_git_ref_syntax(raw: str, label: str) -> str:
    value = clean(raw)
    if not value or value.startswith("-") or any(character.isspace() for character in value):
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
    output = run_git(
        project_root,
        ["merge-base", left, right],
        allow_failure=True,
    )
    assert isinstance(output, str)
    return output.strip() or None


def resolve_git_base(
    project_root: Path,
    *,
    explicit_base_ref: str | None,
    configured_base_ref: object = None,
) -> tuple[str | None, str | None]:
    """Resolve a deterministic, local-only architecture comparison base."""
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
        if git_commit(project_root, candidate) and git_merge_base(
            project_root, "HEAD", candidate
        ):
            return candidate, candidate
    return None, None


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


def validate_affected_paths(
    raw: str,
    rel_path: str,
    findings: list[Finding],
) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in raw.split(","):
        value = item.strip().strip("`").strip()
        if not value:
            continue
        try:
            normalized.append(normalize_repo_relative_glob(value, "Affected paths"))
        except ConfigurationError as exc:
            findings.append(Finding("record-affected-path", rel_path, str(exc)))
    return tuple(normalized)


def configure_task_modes(policy: dict) -> None:
    task_policy = policy.get("tasks")
    if not isinstance(task_policy, dict):
        raise ConfigurationError("registry policy tasks must be an object")
    raw_modes = task_policy.get("modes")
    if raw_modes is None:
        raw_modes = {
            mode: {"required_metadata": list(required)}
            for mode, required in DEFAULT_MODE_REQUIREMENTS.items()
        }
    if not isinstance(raw_modes, dict) or set(raw_modes) != set(MODE_ORDER):
        raise ConfigurationError("tasks.modes must define LIGHT, STANDARD, and STRICT")
    effective_modes: dict[str, list[str]] = {}
    for mode in MODE_ORDER:
        configuration = raw_modes.get(mode)
        if not isinstance(configuration, dict):
            raise ConfigurationError(f"tasks.modes.{mode} must be an object")
        required = configuration.get("required_metadata")
        if not isinstance(required, list) or not required:
            raise ConfigurationError(
                f"tasks.modes.{mode}.required_metadata must be a non-empty array"
            )
        if any(not isinstance(item, str) or not item.strip() for item in required):
            raise ConfigurationError(
                f"tasks.modes.{mode}.required_metadata entries must be non-empty strings"
            )
        missing_baseline = sorted(set(DEFAULT_MODE_REQUIREMENTS[mode]) - set(required))
        if missing_baseline:
            raise ConfigurationError(
                f"tasks.modes.{mode}.required_metadata is missing baseline fields: "
                + ", ".join(missing_baseline)
            )
        effective_modes[mode] = required
    for lower, higher in (("LIGHT", "STANDARD"), ("STANDARD", "STRICT")):
        missing_from_higher = sorted(
            set(effective_modes[lower]) - set(effective_modes[higher])
        )
        if missing_from_higher:
            raise ConfigurationError(
                f"tasks.modes.{higher}.required_metadata must include {lower} fields: "
                + ", ".join(missing_from_higher)
            )
    default_mode = task_policy.get("default_mode", "STANDARD")
    if default_mode not in MODE_ORDER:
        raise ConfigurationError("tasks.default_mode must be LIGHT, STANDARD, or STRICT")

    raw_defaults = task_policy.get("metadata_defaults", DEFAULT_TASK_METADATA_DEFAULTS)
    if not isinstance(raw_defaults, dict):
        raise ConfigurationError("tasks.metadata_defaults must be an object")
    effective_defaults: dict[str, dict[str, str]] = {
        mode: dict(values) for mode, values in DEFAULT_TASK_METADATA_DEFAULTS.items()
    }
    for mode, values in raw_defaults.items():
        if mode not in MODE_ORDER or not isinstance(values, dict):
            raise ConfigurationError(f"invalid tasks.metadata_defaults entry: {mode}")
        normalized: dict[str, str] = {}
        for label, value in values.items():
            if not isinstance(label, str) or not isinstance(value, str):
                raise ConfigurationError(
                    f"tasks.metadata_defaults.{mode} must map strings to strings"
                )
            normalized[label] = value
        if normalized.get("Executable", "false").lower() != "false":
            raise ConfigurationError(
                f"tasks.metadata_defaults.{mode}.Executable must remain false"
            )
        effective_defaults.setdefault(mode, {}).update(normalized)
    task_policy["_effective_modes"] = effective_modes
    task_policy["_effective_metadata_defaults"] = effective_defaults


def compile_secret_pattern(item: object, field_name: str) -> tuple[str, re.Pattern[str]]:
    if not isinstance(item, dict):
        raise ConfigurationError(f"{field_name} entries must be objects with id and regex")
    pattern_id = item.get("id")
    expression = item.get("regex")
    if not isinstance(pattern_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", pattern_id
    ):
        raise ConfigurationError(f"{field_name} pattern id is invalid: {pattern_id!r}")
    if not isinstance(expression, str) or not expression:
        raise ConfigurationError(f"{field_name}.{pattern_id}.regex must be a non-empty string")
    if len(expression) > MAX_SECRET_PATTERN_LENGTH:
        raise ConfigurationError(
            f"{field_name}.{pattern_id}.regex exceeds {MAX_SECRET_PATTERN_LENGTH} characters"
        )
    if NESTED_QUANTIFIER_RE.search(expression):
        raise ConfigurationError(
            f"{field_name}.{pattern_id}.regex contains a potentially unsafe nested quantifier"
        )
    if QUANTIFIED_ALTERNATION_RE.search(expression):
        raise ConfigurationError(
            f"{field_name}.{pattern_id}.regex contains a potentially unsafe "
            "quantified alternation"
        )
    try:
        compiled = re.compile(expression)
    except re.error as exc:
        raise ConfigurationError(
            f"{field_name}.{pattern_id}.regex is invalid: {exc}"
        ) from exc
    return pattern_id, compiled


def configure_secret_scan(policy: dict) -> None:
    trust = policy.get("trust")
    if not isinstance(trust, dict):
        raise ConfigurationError("registry policy trust must be an object")
    for field_name in ("allowed_classifications", "allowed_provenance"):
        allowed = trust.get(field_name)
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(not isinstance(item, str) or not item for item in allowed)
        ):
            raise ConfigurationError(f"trust.{field_name} must be a non-empty string array")
    if trust.get("required_executable") != "false":
        raise ConfigurationError("trust.required_executable must remain false")
    builtin = trust.get("forbidden_patterns")
    if not isinstance(builtin, list):
        raise ConfigurationError("trust.forbidden_patterns must be an array")
    secret_scan = policy.get(
        "secret_scan",
        {"enabled": True, "builtin_patterns": True, "additional_patterns": []},
    )
    if not isinstance(secret_scan, dict):
        raise ConfigurationError("secret_scan must be an object")
    if secret_scan.get("enabled", True) is not True:
        raise ConfigurationError("secret_scan.enabled must remain true")
    if secret_scan.get("builtin_patterns", True) is not True:
        raise ConfigurationError("secret_scan.builtin_patterns must remain true")
    additional = secret_scan.get("additional_patterns", [])
    if not isinstance(additional, list):
        raise ConfigurationError("secret_scan.additional_patterns must be an array")
    if len(additional) > MAX_ADDITIONAL_SECRET_PATTERNS:
        raise ConfigurationError(
            "secret_scan.additional_patterns exceeds "
            f"{MAX_ADDITIONAL_SECRET_PATTERNS} entries"
        )
    mandatory = dict(BUILTIN_SECRET_PATTERNS)
    legacy_additional: list[tuple[str, re.Pattern[str]]] = []
    seen_policy_ids: set[str] = set()
    for item in builtin:
        pattern_id, compiled_pattern = compile_secret_pattern(
            item, "trust.forbidden_patterns"
        )
        if pattern_id in seen_policy_ids:
            raise ConfigurationError(
                f"duplicate trust.forbidden_patterns pattern id: {pattern_id}"
            )
        seen_policy_ids.add(pattern_id)
        if pattern_id in mandatory:
            if compiled_pattern.pattern != mandatory[pattern_id]:
                raise ConfigurationError(
                    "mandatory built-in secret pattern must not be changed: "
                    f"{pattern_id}"
                )
        else:
            legacy_additional.append((pattern_id, compiled_pattern))

    missing = sorted(set(mandatory) - seen_policy_ids)
    if missing:
        raise ConfigurationError(
            "mandatory built-in secret patterns are missing: " + ", ".join(missing)
        )
    if len(legacy_additional) + len(additional) > MAX_ADDITIONAL_SECRET_PATTERNS:
        raise ConfigurationError(
            "combined legacy and secret_scan additional patterns exceed "
            f"{MAX_ADDITIONAL_SECRET_PATTERNS} entries"
        )

    compiled = [
        compile_secret_pattern(
            {"id": pattern_id, "regex": expression}, "built-in secret patterns"
        )
        for pattern_id, expression in BUILTIN_SECRET_PATTERNS
    ]
    compiled.extend(legacy_additional)
    compiled.extend(
        compile_secret_pattern(item, "secret_scan.additional_patterns")
        for item in additional
    )
    identifiers = [pattern_id for pattern_id, _pattern in compiled]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigurationError("secret scan pattern ids must be unique")
    policy["_compiled_secret_patterns"] = compiled


def load_policy(policy_path: Path) -> dict:
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"invalid registry policy: {exc}") from exc
    if not isinstance(policy, dict):
        raise ConfigurationError("registry policy root must be an object")
    if policy.get("schema_version") != 1:
        raise ConfigurationError("unsupported registry policy schema_version")
    configure_task_modes(policy)
    configure_secret_scan(policy)
    architecture_gate = policy.get("architecture_gate")
    if not isinstance(architecture_gate, dict):
        raise ConfigurationError("registry policy architecture_gate must be an object")
    if not isinstance(architecture_gate.get("enabled", True), bool):
        raise ConfigurationError("architecture_gate.enabled must be a boolean")
    branch_template = architecture_gate.get("branch_history_template")
    if not isinstance(branch_template, str):
        raise ConfigurationError("architecture_gate.branch_history_template must be a string")
    without_placeholder = branch_template.replace("{branch_slug}", "", 1)
    if (
        branch_template.count("{branch_slug}") != 1
        or "{" in without_placeholder
        or "}" in without_placeholder
    ):
        raise ConfigurationError(
            "architecture_gate.branch_history_template must contain exactly one "
            "{branch_slug} placeholder"
        )
    architecture_gate["_effective_branch_history_template"] = (
        normalize_repo_relative_glob(
            branch_template, "architecture_gate.branch_history_template"
        )
    )
    no_registry_impacts = architecture_gate.get("no_registry_impacts")
    if (
        not isinstance(no_registry_impacts, list)
        or any(not isinstance(item, str) or not item for item in no_registry_impacts)
    ):
        raise ConfigurationError("architecture_gate.no_registry_impacts must be a string array")
    allowed_impacts = policy["tasks"].get("architecture_impacts")
    if not isinstance(allowed_impacts, list) or any(
        not isinstance(item, str) or not item for item in allowed_impacts
    ):
        raise ConfigurationError("tasks.architecture_impacts must be a string array")
    unknown_no_registry_impacts = sorted(set(no_registry_impacts) - set(allowed_impacts))
    if unknown_no_registry_impacts:
        raise ConfigurationError(
            "architecture_gate.no_registry_impacts contains unknown values: "
            + ", ".join(unknown_no_registry_impacts)
        )
    legacy_sensitive = architecture_gate.get("sensitive_globs")
    default_sensitive = architecture_gate.get("default_sensitive_globs", legacy_sensitive)
    if default_sensitive is None:
        raise ConfigurationError(
            "architecture_gate.default_sensitive_globs (or legacy sensitive_globs) is required"
        )
    architecture_gate["_effective_sensitive_globs"] = [
        *normalize_glob_list(
            default_sensitive, "architecture_gate.default_sensitive_globs"
        ),
        *normalize_glob_list(
            architecture_gate.get("additional_sensitive_globs", []),
            "architecture_gate.additional_sensitive_globs",
        ),
    ]
    architecture_gate["_effective_ignored_globs"] = normalize_glob_list(
        architecture_gate.get("ignored_globs", []),
        "architecture_gate.ignored_globs",
    )
    escalation = architecture_gate.get("mode_escalation", {})
    if not isinstance(escalation, dict):
        raise ConfigurationError("architecture_gate.mode_escalation must be an object")
    minimum = escalation.get("architecture_sensitive_minimum", "STANDARD")
    if minimum not in MODE_ORDER:
        raise ConfigurationError(
            "architecture_gate.mode_escalation.architecture_sensitive_minimum is invalid"
        )
    escalation["_effective_strict_sensitive_globs"] = normalize_glob_list(
        escalation.get("strict_sensitive_globs", []),
        "architecture_gate.mode_escalation.strict_sensitive_globs",
    )
    architecture_gate["_effective_mode_escalation"] = escalation
    return policy


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def is_architecture_sensitive(path: str, architecture_gate: dict) -> bool:
    normalized = path.replace("\\", "/")
    sensitive = architecture_gate["_effective_sensitive_globs"]
    ignored = architecture_gate["_effective_ignored_globs"]
    return matches(normalized, sensitive) and not matches(normalized, ignored)


def relative(agents_root: Path, path: Path) -> str:
    return path.relative_to(agents_root).as_posix()


def read_registry_text(
    path: Path, agents_root: Path, findings: list[Finding]
) -> str | None:
    rel_path = relative(agents_root, path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError:
        findings.append(
            Finding("registry-encoding", rel_path, "registry text must be valid UTF-8")
        )
    except OSError as exc:
        findings.append(
            Finding("registry-read", rel_path, f"registry text could not be read: {exc}")
        )
    return None


def has_symlink_component(root: Path, path: Path) -> bool:
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "`":
        value = value[1:-1].strip()
    return value


def enum_value(value: str) -> str:
    return re.split(r"\s*(?:[;|(]|—)", clean(value).lower(), maxsplit=1)[0].strip("` ")


def parse_metadata(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = METADATA_RE.match(line)
        if match:
            result.setdefault(match.group(1).strip(), clean(match.group(2)))
    return result


def metadata_value(metadata: dict[str, str], label: str) -> str | None:
    for alias in METADATA_ALIASES.get(label, (label,)):
        value = metadata.get(alias)
        if value is not None:
            return value
    return None


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


def scan_symlinks(agents_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for directory in ("tasks", "architecture", "reviews", "policies"):
        root = agents_root / directory
        if root.is_symlink():
            findings.append(
                Finding(
                    "symlink",
                    relative(agents_root, root),
                    "registry data paths must not be symlinks",
                )
            )
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink():
                findings.append(
                    Finding(
                        "symlink",
                        relative(agents_root, path),
                        "registry data paths must not be symlinks",
                    )
                )
    return findings


def scan_tasks(
    agents_root: Path,
    project_root: Path | None,
    policy: dict,
    validation_date: date,
    warnings: list[str],
) -> tuple[list[Finding], list[TaskRecord]]:
    findings: list[Finding] = []
    records: list[TaskRecord] = []
    task_policy = policy["tasks"]
    checkout_branch = current_git_branch(project_root) if project_root is not None else None
    paths = [
        path
        for path in (agents_root / "tasks" / "active").glob("*.md")
        if not has_symlink_component(agents_root, path)
    ]
    paths.extend(
        path
        for path in (agents_root / "tasks" / "history").glob("*/*.md")
        if not has_symlink_component(agents_root, path)
    )
    for path in sorted(paths):
        rel_path = relative(agents_root, path)
        is_active = fnmatch.fnmatchcase(rel_path, "tasks/active/*.md")
        text = read_registry_text(path, agents_root, findings)
        if text is None:
            continue
        metadata = parse_metadata(text)
        affected_paths = validate_affected_paths(
            metadata.get("Affected paths", ""), rel_path, findings
        )
        task_branch = clean(metadata.get("Branch", ""))
        if not is_active:
            relevance = TaskRelevance.HISTORICAL
        elif checkout_branch is None:
            relevance = TaskRelevance.OTHER_ACTIVE
        elif not checkout_branch:
            relevance = TaskRelevance.DETACHED
        elif task_branch == checkout_branch:
            relevance = TaskRelevance.CURRENT
        else:
            relevance = TaskRelevance.OTHER_ACTIVE
        raw_mode = metadata.get("Mode")
        mode: str | None = None
        if raw_mode is not None:
            mode = clean(raw_mode).upper()
            if mode not in MODE_ORDER:
                findings.append(
                    Finding(
                        "task-mode",
                        rel_path,
                        f"Mode must be LIGHT, STANDARD, or STRICT: {raw_mode}",
                    )
                )
                mode = task_policy.get("default_mode", "STANDARD")
            required_metadata = task_policy["_effective_modes"][mode]
        else:
            required_metadata = task_policy.get(
                "required_metadata", task_policy["_effective_modes"]["STANDARD"]
            )

        for label in required_metadata:
            value = metadata_value(metadata, label)
            if value is None or not value.strip():
                findings.append(Finding("task-metadata", rel_path, f"missing metadata: {label}"))

        task_id = metadata_value(metadata, "ID")
        if task_id and task_id != path.stem:
            findings.append(
                Finding(
                    "task-id",
                    rel_path,
                    f"ID '{task_id}' does not match filename '{path.stem}'",
                )
            )

        state = enum_value(metadata_value(metadata, "Status") or "")
        if state and state not in task_policy["canonical_states"]:
            findings.append(Finding("task-state", rel_path, f"unknown canonical state: {state}"))
        for pattern, allowed in task_policy["directory_states"].items():
            if fnmatch.fnmatchcase(rel_path, pattern) and state and state not in allowed:
                findings.append(
                    Finding(
                        "task-state-directory",
                        rel_path,
                        f"state '{state}' is invalid here; allowed: {', '.join(allowed)}",
                    )
                )

        gate = enum_value(metadata.get("Delivery gate", ""))
        if gate and gate not in task_policy["delivery_gates"]:
            findings.append(Finding("delivery-gate", rel_path, f"unknown delivery gate: {gate}"))

        impact = enum_value(metadata_value(metadata, "Architecture impact") or "")
        if impact and impact not in task_policy["architecture_impacts"]:
            findings.append(
                Finding("architecture-impact", rel_path, f"unknown architecture impact: {impact}")
            )

        source_commit = metadata.get("Source commit")
        if source_commit:
            validate_source_commit(project_root, source_commit, rel_path, findings)
        legacy_base = metadata.get("Base ref / merge-base")
        if legacy_base:
            validate_base_ref(
                project_root,
                legacy_base,
                rel_path,
                findings,
                label="Base ref / merge-base",
            )
        base_ref = metadata.get("Base ref")
        merge_base = metadata.get("Merge-base")
        current_head = metadata.get("Current head")
        resolved_base = None
        resolved_current_head = None
        if base_ref:
            resolved_base = validate_base_ref(
                project_root, base_ref, rel_path, findings, label="Base ref"
            )
        if current_head:
            resolved_current_head = validate_commit_id(
                project_root,
                current_head,
                "Current head",
                "record-current-head",
                rel_path,
                findings,
            )
        if merge_base and resolved_base and resolved_current_head:
            validate_merge_base(
                project_root,
                clean(base_ref or ""),
                resolved_current_head,
                merge_base,
                rel_path,
                findings,
            )
        elif merge_base:
            validate_commit_id(
                project_root,
                merge_base,
                "Merge-base",
                "record-merge-base",
                rel_path,
                findings,
            )
        if metadata.get("Related task"):
            validate_related_task(agents_root, metadata["Related task"], rel_path, findings)
        architecture_links = validate_related_architecture_records(
            agents_root,
            metadata.get("Related architecture records", ""),
            rel_path,
            findings,
        )
        if (
            is_active
            and relevance is TaskRelevance.DETACHED
            and metadata.get("Branch")
        ):
            warnings.append(
                f"{rel_path}: checkout-relative Branch and Current head checks "
                "skipped because HEAD is detached"
            )
        if (
            relevance is TaskRelevance.CURRENT
            and project_root is not None
            and resolved_current_head
        ):
            checkout_head = current_git_head(project_root)
            if resolved_current_head != checkout_head:
                findings.append(
                    Finding(
                        "record-current-head",
                        rel_path,
                        f"Current head is {resolved_current_head}, expected {checkout_head}",
                    )
                )
        created = metadata_value(metadata, "Created")
        updated = metadata_value(metadata, "Updated")
        if created:
            validate_iso_date(created, "Created", rel_path, findings, validation_date)
        if updated:
            validate_iso_date(updated, "Updated", rel_path, findings, validation_date)
        if created and updated:
            try:
                if date.fromisoformat(clean(updated)) < date.fromisoformat(clean(created)):
                    findings.append(
                        Finding("task-date-order", rel_path, "Updated must be on or after Created")
                    )
            except ValueError:
                pass
        records.append(
            TaskRecord(
                path=path,
                rel_path=rel_path,
                metadata=metadata,
                relevance=relevance,
                architecture_links=architecture_links,
                affected_paths=affected_paths,
            )
        )
    return findings, records


def validate_iso_date(
    raw: str,
    label: str,
    rel_path: str,
    findings: list[Finding],
    validation_date: date,
) -> date | None:
    try:
        parsed = date.fromisoformat(clean(raw))
    except ValueError:
        findings.append(Finding("record-date", rel_path, f"{label} must be an ISO date: {raw}"))
        return None
    if parsed > validation_date:
        findings.append(
            Finding("record-date", rel_path, f"{label} must not be in the future: {raw}")
        )
        return None
    return parsed


def compute_record_age(verified_at: date, validation_date: date) -> int:
    return (validation_date - verified_at).days


def validate_source_commit(
    project_root: Path | None, raw: str, rel_path: str, findings: list[Finding]
) -> None:
    validate_commit_id(
        project_root,
        raw,
        "Source commit",
        "record-source-commit",
        rel_path,
        findings,
    )


def validate_commit_id(
    project_root: Path | None,
    raw: str,
    label: str,
    code: str,
    rel_path: str,
    findings: list[Finding],
) -> str | None:
    value = clean(raw).lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", value):
        findings.append(
            Finding(code, rel_path, f"{label} must be a 7-40 character commit ID")
        )
        return None
    if project_root is None:
        return value
    resolved = git_commit(project_root, value)
    if not resolved:
        findings.append(Finding(code, rel_path, f"{label} does not resolve to a commit: {value}"))
        return None
    return resolved


def validate_base_ref(
    project_root: Path | None,
    raw: str,
    rel_path: str,
    findings: list[Finding],
    *,
    label: str = "Base ref",
) -> str | None:
    value = clean(raw)
    if not value or value.startswith("-") or any(character.isspace() for character in value):
        findings.append(Finding("record-base-ref", rel_path, f"{label} is unsafe: {raw!r}"))
        return None
    if project_root is None:
        return value
    resolved = git_commit(project_root, value)
    if not resolved:
        findings.append(
            Finding("record-base-ref", rel_path, f"{label} does not resolve to a commit: {value}")
        )
        return None
    return resolved


def current_git_branch(project_root: Path) -> str:
    output = run_git(project_root, ["branch", "--show-current"])
    assert isinstance(output, str)
    return output.strip()


def current_git_head(project_root: Path) -> str:
    output = run_git(project_root, ["rev-parse", "HEAD"])
    assert isinstance(output, str)
    return output.strip()


def related_task_candidates(agents_root: Path, raw: str) -> tuple[list[Path], str | None]:
    value = unquote(clean(raw)).replace("\\", "/")
    value = value.split("#", 1)[0].split("?", 1)[0]
    if not value:
        return [], "Related task must not be empty"
    if "\0" in value:
        return [], "Related task must not contain a NUL byte"
    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//") or value.startswith("/"):
        return [], f"Related task must be a repository registry reference: {raw}"
    if value.startswith(".agents/"):
        value = value[len(".agents/") :]
    reference = PurePosixPath(value)
    if ".." in reference.parts or "." in reference.parts:
        return [], f"Related task contains traversal: {raw}"
    if len(reference.parts) == 1:
        stem = reference.stem if reference.suffix == ".md" else value
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", stem):
            return [], f"Related task ID is invalid: {raw}"
        candidates = [agents_root / "tasks" / "active" / f"{stem}.md"]
        candidates.extend((agents_root / "tasks" / "history").glob(f"*/{stem}.md"))
        return candidates, None
    allowed = (
        len(reference.parts) == 3
        and reference.parts[:2] == ("tasks", "active")
        or len(reference.parts) == 4
        and reference.parts[:2] == ("tasks", "history")
    )
    if not allowed or reference.suffix != ".md":
        return [], f"Related task must point inside tasks/active or tasks/history: {raw}"
    return [agents_root / reference], None


def validate_related_task(
    agents_root: Path,
    raw: str,
    rel_path: str,
    findings: list[Finding],
) -> None:
    _path, error = resolve_related_task_reference(agents_root, raw)
    if error:
        findings.append(Finding("record-related-task", rel_path, error))


def resolve_related_task_reference(
    agents_root: Path, raw: str
) -> tuple[Path | None, str | None]:
    candidates, error = related_task_candidates(agents_root, raw)
    if error:
        return None, error
    if any(has_symlink_component(agents_root, path) for path in candidates):
        return None, f"Related task must not use a symlink: {clean(raw)}"
    existing = [path for path in candidates if path.is_file()]
    if len(existing) != 1:
        detail = "does not exist" if not existing else "is ambiguous"
        return None, f"Related task {detail}: {clean(raw)}"
    return existing[0], None


def architecture_reference_values(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    for item in raw.strip().split(","):
        value = item.strip().strip("`").strip()
        if value:
            values.append(value)
    if not values or (
        len(values) == 1
        and values[0].lower() in {"none", "n/a", "not-applicable"}
    ):
        return ()
    return tuple(values)


def architecture_record_candidates(
    agents_root: Path, raw: str
) -> tuple[list[Path], str | None]:
    value = unquote(clean(raw)).replace("\\", "/")
    value = value.split("#", 1)[0].split("?", 1)[0]
    if not value:
        return [], "architecture record reference must not be empty"
    if "\0" in value:
        return [], "architecture record reference must not contain a NUL byte"
    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//") or value.startswith("/"):
        return [], f"architecture record must be a repository registry reference: {raw}"
    if value.startswith(".agents/"):
        value = value[len(".agents/") :]
    reference = PurePosixPath(value)
    if ".." in reference.parts or "." in reference.parts:
        return [], f"architecture record reference contains traversal: {raw}"
    if len(reference.parts) == 1:
        stem = reference.stem if reference.suffix == ".md" else value
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", stem):
            return [], f"architecture record ID is invalid: {raw}"
        return [
            agents_root / "architecture" / "branches" / f"{stem}.md",
            agents_root / "architecture" / "changes" / f"{stem}.md",
        ], None
    allowed = (
        len(reference.parts) == 3
        and reference.parts[:2] in {
            ("architecture", "branches"),
            ("architecture", "changes"),
        }
    )
    if not allowed or reference.suffix != ".md":
        return [], (
            "architecture record must point inside architecture/branches or "
            f"architecture/changes and use .md: {raw}"
        )
    return [agents_root / reference], None


def resolve_architecture_record_reference(
    agents_root: Path, raw: str
) -> tuple[Path | None, str | None]:
    candidates, error = architecture_record_candidates(agents_root, raw)
    if error:
        return None, error
    change_index = agents_root / "architecture" / "changes" / "index.md"
    if change_index in candidates:
        return None, "architecture/changes/index is not an architecture record"
    if any(has_symlink_component(agents_root, path) for path in candidates):
        return None, f"architecture record reference must not use a symlink: {clean(raw)}"
    existing = [path for path in candidates if path.is_file()]
    if len(existing) != 1:
        detail = "does not exist" if not existing else "is ambiguous"
        return None, f"architecture record reference {detail}: {clean(raw)}"
    return existing[0], None


def validate_related_architecture_records(
    agents_root: Path,
    raw: str,
    rel_path: str,
    findings: list[Finding],
) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for reference in architecture_reference_values(raw):
        path, error = resolve_architecture_record_reference(agents_root, reference)
        if error:
            findings.append(Finding("record-related-architecture", rel_path, error))
        elif path is not None:
            resolved.append(path)
    return tuple(resolved)


def validate_merge_base(
    project_root: Path | None,
    base_ref: str,
    head_commit: str,
    raw_merge_base: str,
    rel_path: str,
    findings: list[Finding],
) -> None:
    merge_base = validate_commit_id(
        project_root,
        raw_merge_base,
        "Merge-base",
        "record-merge-base",
        rel_path,
        findings,
    )
    if project_root is None or merge_base is None:
        return
    output = run_git(
        project_root,
        ["merge-base", base_ref, head_commit],
        allow_failure=True,
    )
    assert isinstance(output, str)
    expected = output.strip()
    if not expected:
        findings.append(
            Finding(
                "record-merge-base",
                rel_path,
                "Merge-base cannot be computed from Base ref and Current head",
            )
        )
    elif merge_base != expected:
        findings.append(
            Finding(
                "record-merge-base",
                rel_path,
                f"Merge-base is {merge_base}, expected {expected}",
            )
        )


def classify_architecture_record_relevance(
    path: Path,
    kind: str,
    metadata: dict[str, str],
    checkout_branch: str | None,
    current_links: set[Path],
    other_links: set[Path],
    other_active_branches: set[str],
) -> RecordRelevance:
    if path in current_links:
        return RecordRelevance.LINKED
    if (
        kind == "branch"
        and checkout_branch
        and clean(metadata.get("Branch", "")) == checkout_branch
    ):
        return RecordRelevance.CURRENT
    if path in other_links or (
        kind == "branch"
        and clean(metadata.get("Branch", "")) in other_active_branches
    ):
        return RecordRelevance.ACTIVE_OTHER
    return RecordRelevance.HISTORICAL


def scan_records(
    agents_root: Path,
    project_root: Path | None,
    policy: dict,
    validation_date: date,
    warnings: list[str],
    task_records: Sequence[TaskRecord],
) -> tuple[list[Finding], list[ArchitectureRecord]]:
    findings: list[Finding] = []
    records: list[ArchitectureRecord] = []
    record_policy = policy["records"]
    checkout_branch = current_git_branch(project_root) if project_root is not None else None
    current_links = {
        linked
        for task in task_records
        if task.relevance is TaskRelevance.CURRENT
        for linked in task.architecture_links
    }
    other_links = {
        linked
        for task in task_records
        if task.relevance in {TaskRelevance.OTHER_ACTIVE, TaskRelevance.DETACHED}
        for linked in task.architecture_links
    }
    other_active_branches = {
        clean(task.metadata.get("Branch", ""))
        for task in task_records
        if task.relevance is TaskRelevance.OTHER_ACTIVE
    }
    rules = (
        (
            sorted(
                path
                for path in (agents_root / "architecture" / "branches").glob("*.md")
                if not has_symlink_component(agents_root, path)
            ),
            record_policy["branch_required_metadata"],
            "branch",
        ),
        (
            [
                path
                for path in sorted((agents_root / "architecture" / "changes").glob("*.md"))
                if path.name != "index.md"
                and not has_symlink_component(agents_root, path)
            ],
            record_policy["change_required_metadata"],
            "change",
        ),
        )
    for paths, required, kind in rules:
        for path in paths:
            rel_path = relative(agents_root, path)
            text = read_registry_text(path, agents_root, findings)
            if text is None:
                continue
            metadata = parse_metadata(text)
            affected_paths = validate_affected_paths(
                metadata.get("Affected paths", ""), rel_path, findings
            )
            relevance = classify_architecture_record_relevance(
                path,
                kind,
                metadata,
                checkout_branch,
                current_links,
                other_links,
                other_active_branches,
            )
            related_task_path: Path | None = None
            for label in required:
                if not metadata_value(metadata, label):
                    findings.append(
                        Finding("record-metadata", rel_path, f"missing metadata: {label}")
                    )
            source_commit = metadata.get("Source commit")
            if source_commit:
                validate_source_commit(project_root, source_commit, rel_path, findings)
            if metadata.get("Related task"):
                related_task_path, related_task_error = resolve_related_task_reference(
                    agents_root, metadata["Related task"]
                )
                if related_task_error:
                    findings.append(
                        Finding("record-related-task", rel_path, related_task_error)
                    )
            if metadata.get("Base ref / merge-base"):
                validate_base_ref(
                    project_root,
                    metadata["Base ref / merge-base"],
                    rel_path,
                    findings,
                    label="Base ref / merge-base",
                )
            parsed_dates: dict[str, date] = {}
            for label in ("Created", "Updated", "Date", "Verified at"):
                if metadata.get(label):
                    parsed = validate_iso_date(
                        metadata[label], label, rel_path, findings, validation_date
                    )
                    if parsed is not None:
                        parsed_dates[label] = parsed

            stale_after_days: int | None = None
            stale_age_days: int | None = None
            stale_after = clean(metadata.get("Stale after days", ""))
            if stale_after:
                try:
                    stale_after_days = int(stale_after)
                    if stale_after_days <= 0:
                        raise ValueError
                except ValueError:
                    findings.append(
                        Finding(
                            "record-stale-threshold",
                            rel_path,
                            "Stale after days must be a positive integer",
                        )
                    )
                    stale_after_days = None
            if stale_after_days is not None and "Verified at" in parsed_dates:
                stale_age_days = compute_record_age(
                    parsed_dates["Verified at"], validation_date
                )
                if stale_age_days > stale_after_days:
                    message = (
                        f"{relevance.value} architecture record is stale "
                        f"({stale_age_days} days > {stale_after_days} days)"
                    )
                    if relevance in {
                        RecordRelevance.CURRENT,
                        RecordRelevance.LINKED,
                    }:
                        findings.append(Finding("record-stale", rel_path, message))
                    else:
                        warnings.append(f"[record-stale] {rel_path}: {message}")
            if kind == "change":
                if metadata.get("Head snapshot"):
                    validate_commit_id(
                        project_root,
                        metadata["Head snapshot"],
                        "Head snapshot",
                        "record-head-snapshot",
                        rel_path,
                        findings,
                    )
                record_id = clean(metadata.get("ID", ""))
                if record_id and record_id != path.stem:
                    findings.append(
                        Finding(
                            "record-id",
                            rel_path,
                            f"ID '{record_id}' does not match filename '{path.stem}'",
                        )
                    )
                status = enum_value(metadata.get("Status", ""))
                if status and status not in record_policy["change_statuses"]:
                    findings.append(
                        Finding(
                            "record-status",
                            rel_path,
                            f"unknown change status: {status}",
                        )
                    )
                gate = enum_value(metadata.get("Delivery gate", ""))
                if gate and gate not in policy["tasks"]["delivery_gates"]:
                    findings.append(
                        Finding(
                            "delivery-gate",
                            rel_path,
                            f"unknown delivery gate: {gate}",
                        )
                    )
            else:
                branch = clean(metadata.get("Branch", ""))
                slug = clean(metadata.get("Slug", ""))
                if slug and slug != path.stem:
                    findings.append(
                        Finding(
                            "record-slug",
                            rel_path,
                            f"Slug '{slug}' does not match filename '{path.stem}'",
                        )
                    )
                expected_slug = branch.replace("/", "-")
                if branch and slug and slug != expected_slug:
                    findings.append(
                        Finding(
                            "record-branch-slug",
                            rel_path,
                            f"Slug '{slug}' does not match Branch-derived slug '{expected_slug}'",
                        )
                    )
                base_ref = metadata.get("Base ref")
                current_head = metadata.get("Current head")
                merge_base = metadata.get("Merge-base")
                resolved_base = None
                resolved_head = None
                if base_ref:
                    resolved_base = validate_base_ref(
                        project_root, base_ref, rel_path, findings
                    )
                if current_head:
                    resolved_head = validate_commit_id(
                        project_root,
                        current_head,
                        "Current head",
                        "record-current-head",
                        rel_path,
                        findings,
                    )
                if merge_base and resolved_base and resolved_head:
                    validate_merge_base(
                        project_root,
                        clean(base_ref or ""),
                        resolved_head,
                        merge_base,
                        rel_path,
                        findings,
                    )
                elif merge_base:
                    validate_commit_id(
                        project_root,
                        merge_base,
                        "Merge-base",
                        "record-merge-base",
                        rel_path,
                        findings,
                    )
                if project_root is not None and branch:
                    if not checkout_branch:
                        warnings.append(
                            f"{rel_path}: Current head comparison skipped because HEAD is detached"
                        )
                    elif branch == checkout_branch and resolved_head:
                        checkout_head = current_git_head(project_root)
                        if resolved_head != checkout_head:
                            findings.append(
                                Finding(
                                    "record-current-head",
                                    rel_path,
                                    f"Current head is {resolved_head}, expected {checkout_head}",
                                )
                            )
            records.append(
                ArchitectureRecord(
                    path=path,
                    rel_path=rel_path,
                    kind=kind,
                    metadata=metadata,
                    relevance=relevance,
                    verified_at=parsed_dates.get("Verified at"),
                    stale_after_days=stale_after_days,
                    stale_age_days=stale_age_days,
                    related_task_path=related_task_path,
                    affected_paths=affected_paths,
                )
            )
    return findings, records


def validate_architecture_relationships(
    task_records: Sequence[TaskRecord],
    architecture_records: Sequence[ArchitectureRecord],
    warnings: list[str],
) -> list[Finding]:
    findings: list[Finding] = []
    records_by_path = {record.path: record for record in architecture_records}

    def requires_bidirectional_error(task: TaskRecord) -> bool:
        raw_mode = task.metadata.get("Mode")
        mode = clean(raw_mode).upper() if raw_mode else None
        impact = enum_value(metadata_value(task.metadata, "Architecture impact") or "")
        return impact == "confirmed" or mode == "STRICT" or raw_mode is not None

    for task in task_records:
        linked_changes = [
            records_by_path[path]
            for path in task.architecture_links
            if path in records_by_path and records_by_path[path].kind == "change"
        ]
        impact = enum_value(metadata_value(task.metadata, "Architecture impact") or "")
        if impact == "confirmed" and not linked_changes:
            findings.append(
                Finding(
                    "architecture-change-evidence",
                    task.rel_path,
                    "confirmed Architecture impact requires a dedicated architecture "
                    "change record in Related architecture records",
                )
            )
        for record in linked_changes:
            if record.related_task_path == task.path:
                continue
            task_id = clean(metadata_value(task.metadata, "ID") or task.path.stem)
            message = (
                f"{record.rel_path} does not link back to task {task_id} through "
                "Related task"
            )
            if requires_bidirectional_error(task):
                findings.append(
                    Finding("architecture-link-mismatch", task.rel_path, message)
                )
            else:
                warnings.append(
                    f"[architecture-link-mismatch] {task.rel_path}: legacy task link "
                    f"is not bidirectional: {message}"
                )
    tasks_by_path = {task.path: task for task in task_records}
    for record in architecture_records:
        if record.kind != "change" or record.related_task_path not in tasks_by_path:
            continue
        task = tasks_by_path[record.related_task_path]
        if record.path in task.architecture_links:
            continue
        task_id = clean(metadata_value(task.metadata, "ID") or task.path.stem)
        message = (
            f"Related task points to {task_id}, but {task.rel_path} does not link to "
            f"{record.rel_path} through Related architecture records"
        )
        if requires_bidirectional_error(task):
            findings.append(
                Finding("architecture-link-mismatch", record.rel_path, message)
            )
        else:
            warnings.append(
                f"[architecture-link-mismatch] {record.rel_path}: legacy task link "
                f"is not bidirectional: {message}"
            )
    return findings


def registry_record(path: str) -> bool:
    return matches(
        path,
        (
            "tasks/active/*.md",
            "tasks/history/*/*.md",
            "architecture/branches/*.md",
            "architecture/changes/*.md",
        ),
    ) and path != "architecture/changes/index.md"


def scan_trust_and_secrets(agents_root: Path, files: list[Path], policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    trust = policy["trust"]
    patterns = policy["_compiled_secret_patterns"]
    for path in files:
        rel_path = relative(agents_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if path.suffix.lower() in REGISTRY_TEXT_SUFFIXES:
                findings.append(
                    Finding(
                        "registry-encoding",
                        rel_path,
                        "registry text must be valid UTF-8",
                    )
                )
            continue
        except OSError as exc:
            findings.append(
                Finding(
                    "registry-read",
                    rel_path,
                    f"registry data could not be read: {exc}",
                )
            )
            continue
        if registry_record(rel_path):
            metadata = parse_metadata(text)
            defaults: dict[str, str] = {}
            if rel_path.startswith("tasks/") and metadata.get("Mode"):
                mode = clean(metadata["Mode"]).upper()
                defaults = policy["tasks"]["_effective_metadata_defaults"].get(mode, {})

            def trust_value(label: str) -> str:
                explicit = clean(metadata.get(label, ""))
                return (explicit or clean(defaults.get(label, ""))).lower()

            classification = trust_value("Data classification")
            provenance = trust_value("Provenance")
            executable = trust_value("Executable")
            if classification not in trust["allowed_classifications"]:
                findings.append(
                    Finding(
                        "data-classification",
                        rel_path,
                        "missing or invalid classification",
                    )
                )
            if provenance not in trust["allowed_provenance"]:
                findings.append(Finding("provenance", rel_path, "missing or invalid provenance"))
            if executable != trust["required_executable"]:
                findings.append(Finding("executable", rel_path, "Executable must be false"))
        for pattern_id, pattern in patterns:
            if pattern.search(text):
                findings.append(
                    Finding("sensitive-data", rel_path, f"matched forbidden pattern: {pattern_id}")
                )
    return findings


def markdown_targets(text: str) -> list[str]:
    return [*INLINE_LINK_RE.findall(text), *REFERENCE_LINK_RE.findall(text)]


def scan_links(agents_root: Path, project_root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        rel_path = relative(agents_root, path)
        text = read_registry_text(path, agents_root, findings)
        if text is None:
            continue
        for raw in markdown_targets(text):
            target = raw.strip()
            if target.startswith("<") and ">" in target:
                target = target[1 : target.index(">")]
            else:
                target = target.split(maxsplit=1)[0]
            target = unquote(target)
            parsed = urlparse(target)
            if not target or target.startswith("#") or parsed.scheme or target.startswith("//"):
                continue
            if "\0" in target:
                findings.append(Finding("link-target", rel_path, "link contains a NUL byte"))
                continue
            if target.startswith("/") or "YYYYMMDD" in target:
                continue
            target = target.split("#", 1)[0].split("?", 1)[0]
            candidate = (path.parent / PurePosixPath(posixpath.normpath(target))).resolve()
            try:
                candidate.relative_to(project_root)
            except ValueError:
                findings.append(Finding("link-escape", rel_path, f"link escapes repository: {raw}"))
                continue
            if not candidate.exists():
                findings.append(
                    Finding(
                        "broken-link",
                        rel_path,
                        f"link target does not exist: {raw}",
                    )
                )
    return findings


def scan_context_budget(agents_root: Path, files: list[Path], policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    budget = policy["context_budget"]
    included = [path for path in files if path.suffix.lower() in {".md", ".yml", ".yaml"}]
    total = sum(path.stat().st_size for path in included)
    if total > budget["total_bytes"]:
        findings.append(Finding("context-total", ".", f"registry uses {total} bytes"))
    for path in included:
        rel_path = relative(agents_root, path)
        limit = budget["default_file_bytes"]
        for pattern, configured in budget["path_limits"].items():
            if fnmatch.fnmatchcase(rel_path, pattern):
                limit = configured
                break
        size = path.stat().st_size
        if size > limit:
            findings.append(Finding("context-file", rel_path, f"{size} bytes exceeds {limit}"))
    return findings


def parse_name_status(raw: bytes) -> dict[str, set[str]]:
    tokens = [
        token.decode("utf-8", errors="replace")
        for token in raw.split(b"\0")
        if token
    ]
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


def covered_by_patterns(path: str, affected_paths: Iterable[str]) -> bool:
    for raw in affected_paths:
        pattern = clean(raw)
        if not pattern or pattern in {"*", "**", "**/*", ".", "/"}:
            continue
        if path == pattern or fnmatch.fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"):
            return True
    return False


def effective_task_mode(metadata: dict[str, str], policy: dict) -> str | None:
    raw = metadata.get("Mode")
    if raw is None:
        return "STANDARD"
    mode = clean(raw).upper()
    return mode if mode in MODE_ORDER else None


def required_mode_for_path(path: str, architecture_gate: dict) -> str:
    escalation = architecture_gate["_effective_mode_escalation"]
    if matches(path, escalation["_effective_strict_sensitive_globs"]):
        return "STRICT"
    return escalation.get("architecture_sensitive_minimum", "STANDARD")


def scan_architecture_gate(
    project_root: Path,
    agents_root: Path,
    base_ref: str,
    policy: dict,
    existing_findings: list[Finding],
    task_records: Sequence[TaskRecord],
    architecture_records: Sequence[ArchitectureRecord],
) -> tuple[list[Finding], list[str]]:
    changes = changed_entries(project_root, base_ref)
    gate = policy["architecture_gate"]
    sensitive = sorted(
        path
        for path in changes
        if not path.startswith(".agents/") and is_architecture_sensitive(path, gate)
    )
    if not sensitive:
        return [], ["no architecture-sensitive project path changed"]

    branch_output = run_git(project_root, ["branch", "--show-current"])
    assert isinstance(branch_output, str)
    branch = branch_output.strip()
    if not branch:
        raise ConfigurationError("detached HEAD cannot be mapped to a branch architecture record")

    branch_rel = gate["_effective_branch_history_template"].format(
        branch_slug=branch.replace("/", "-")
    )
    branch_repo_path = f".agents/{branch_rel}"
    branch_path = agents_root / branch_rel
    invalid_paths = {finding.path for finding in existing_findings}
    records_by_path = {record.path: record for record in architecture_records}
    branch_record = records_by_path.get(branch_path)
    branch_evidence = bool(
        branch_record is not None
        and branch_record.kind == "branch"
        and branch_record.rel_path not in invalid_paths
        and changes.get(branch_repo_path, set()).intersection({"A", "M"})
    )

    gate_findings: list[Finding] = []
    notes: list[str] = []
    freshness_reported: set[str] = set()
    current_tasks = [
        task
        for task in task_records
        if task.relevance is TaskRelevance.CURRENT
        and task.rel_path not in invalid_paths
    ]

    # LIGHT records do not carry Branch metadata, but sensitive paths must still
    # produce the actionable promotion finding instead of looking like no task exists.
    for task in task_records:
        if task.relevance is TaskRelevance.HISTORICAL or task.rel_path in invalid_paths:
            continue
        if (
            task.relevance is TaskRelevance.OTHER_ACTIVE
            and clean(task.metadata.get("Branch", ""))
        ):
            continue
        mode = effective_task_mode(task.metadata, policy)
        if mode != "LIGHT":
            continue
        covered_sensitive = [
            path for path in sensitive if covered_by_patterns(path, task.affected_paths)
        ]
        if not covered_sensitive:
            continue
        required_mode = max(
            (required_mode_for_path(path, gate) for path in covered_sensitive),
            key=MODE_ORDER.__getitem__,
        )
        if MODE_ORDER[mode] < MODE_ORDER[required_mode]:
            gate_findings.append(
                Finding(
                    "task-mode-escalation",
                    task.rel_path,
                    f"Mode {mode} must be promoted to {required_mode} for sensitive paths: "
                    + ", ".join(covered_sensitive),
                )
            )

    uncovered = set(sensitive)
    used_no_impact = False
    used_branch_evidence = False
    used_change_evidence = False
    for task in current_tasks:
        covered_sensitive = sorted(
            path for path in sensitive if covered_by_patterns(path, task.affected_paths)
        )
        if not covered_sensitive:
            continue
        mode = effective_task_mode(task.metadata, policy)
        if mode is None:
            continue
        required_mode = max(
            (required_mode_for_path(path, gate) for path in covered_sensitive),
            key=MODE_ORDER.__getitem__,
        )
        if MODE_ORDER[mode] < MODE_ORDER[required_mode]:
            gate_findings.append(
                Finding(
                    "task-mode-escalation",
                    task.rel_path,
                    f"Mode {mode} must be promoted to {required_mode} for sensitive paths: "
                    + ", ".join(covered_sensitive),
                )
            )
            continue

        impact = enum_value(metadata_value(task.metadata, "Architecture impact") or "")
        linked_changes = [
            records_by_path[path]
            for path in task.architecture_links
            if path in records_by_path and records_by_path[path].kind == "change"
        ]
        usable_change_paths: set[str] = set()
        wrong_branch_records: list[str] = []
        for record in linked_changes:
            if record.rel_path in invalid_paths or record.related_task_path != task.path:
                continue
            record_branch = clean(record.metadata.get("Branch", ""))
            if record_branch != branch:
                wrong_branch_records.append(
                    f"{record.rel_path} ({record_branch or 'missing Branch'})"
                )
                continue
            if record.stale_after_days is None:
                if record.rel_path not in freshness_reported:
                    gate_findings.append(
                        Finding(
                            "architecture-evidence-freshness",
                            record.rel_path,
                            "gate-required architecture change evidence must declare "
                            "a positive Stale after days value",
                        )
                    )
                    freshness_reported.add(record.rel_path)
                continue
            if (
                record.verified_at is None
                or record.stale_age_days is None
                or record.stale_age_days > record.stale_after_days
            ):
                continue
            usable_change_paths.update(
                path
                for path in covered_sensitive
                if covered_by_patterns(path, record.affected_paths)
            )

        requires_change_record = impact == "confirmed" or mode == "STRICT"
        if requires_change_record:
            missing_change_paths = sorted(set(covered_sensitive) - usable_change_paths)
            if missing_change_paths:
                requirement = (
                    "STRICT sensitive work"
                    if mode == "STRICT" and impact != "confirmed"
                    else "confirmed Architecture impact"
                )
                message = (
                    f"{requirement} requires valid, fresh, linked architecture/changes "
                    "evidence covering: "
                    + ", ".join(missing_change_paths)
                )
                if wrong_branch_records:
                    message += "; linked records do not match current branch: " + ", ".join(
                        wrong_branch_records
                    )
                gate_findings.append(
                    Finding("architecture-change-evidence", task.rel_path, message)
                )
            if usable_change_paths:
                uncovered.difference_update(usable_change_paths)
                used_change_evidence = True
            continue

        if impact in gate["no_registry_impacts"]:
            uncovered.difference_update(covered_sensitive)
            used_no_impact = True
            continue

        if usable_change_paths:
            uncovered.difference_update(usable_change_paths)
            used_change_evidence = True
        if (
            impact in {"possible", "architecture-change"}
            and branch_evidence
            and (
                branch_path in task.architecture_links
                or task.metadata.get("Mode") is None
            )
        ):
            uncovered.difference_update(covered_sensitive)
            used_branch_evidence = True

    if uncovered:
        gate_findings.append(
            Finding(
                "architecture-registry-missing",
                branch_repo_path,
                "architecture-sensitive paths are not covered by valid current-branch "
                "task evidence: "
                + ", ".join(sorted(uncovered)),
            )
        )
    if used_change_evidence:
        notes.append("validated architecture change evidence for current-branch tasks")
    if used_branch_evidence:
        notes.append(f"validated branch architecture evidence: {branch_repo_path}")
    if used_no_impact:
        notes.append("architecture-sensitive paths are covered by current no-impact task records")
    return gate_findings, notes


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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    notes: list[str] = []
    warnings: list[str] = []
    try:
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
            raise ConfigurationError(
                f"registry policy path must not contain a symlink: {policy_path}"
            )
        policy = load_policy(policy_path)

        files = data_files(agents_root)
        findings: list[Finding] = []
        findings.extend(scan_symlinks(agents_root))
        task_findings, task_records = scan_tasks(
            agents_root, git_root, policy, validation_date, warnings
        )
        findings.extend(task_findings)
        record_findings, architecture_records = scan_records(
            agents_root,
            git_root,
            policy,
            validation_date,
            warnings,
            task_records,
        )
        findings.extend(record_findings)
        findings.extend(
            validate_architecture_relationships(
                task_records,
                architecture_records,
                warnings,
            )
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
            warnings.append(
                "architecture gate skipped: --project is not inside a Git repository"
            )
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
                warnings.append(
                    "architecture gate skipped: no safe base reference could be resolved"
                )
            else:
                notes.append(f"architecture base resolved from {base_source}: {base_ref}")
                if not current_git_branch(git_root):
                    warnings.append(
                        "architecture gate skipped: checkout-relative evidence cannot "
                        "be selected because HEAD is detached"
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
            warnings.append(
                "Git metadata object validation skipped: no Git repository was resolved"
            )
        findings = sorted(set(findings))
        warnings = sorted(set(warnings))
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
