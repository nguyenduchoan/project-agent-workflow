"""Load and validate registry policy using only the Python standard library."""

from __future__ import annotations

import json
import re
from pathlib import Path

from registry.findings import ConfigurationError
from registry.paths import normalize_glob_list, normalize_repo_relative_glob


MODE_ORDER = {"LIGHT": 0, "STANDARD": 1, "STRICT": 2}
DEFAULT_MODE_REQUIREMENTS = {
    "LIGHT": (
        "ID", "Status", "Created", "Updated", "Affected paths",
        "Acceptance criteria", "Validation", "Architecture impact",
    ),
    "STANDARD": (
        "ID", "Status", "Created", "Updated", "Affected paths",
        "Acceptance criteria", "Validation", "Architecture impact", "Branch",
        "Base ref", "Source commit", "Risks", "Dependencies",
        "Related architecture records", "Review notes",
    ),
    "STRICT": (
        "ID", "Status", "Created", "Updated", "Affected paths",
        "Acceptance criteria", "Validation", "Architecture impact", "Branch",
        "Base ref", "Source commit", "Risks", "Dependencies",
        "Related architecture records", "Review notes", "Owner", "Reviewer",
        "Delivery gate", "Merge-base", "Current head", "Rollout", "Rollback",
        "Evidence", "Data classification", "Provenance",
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
        missing = sorted(set(effective_modes[lower]) - set(effective_modes[higher]))
        if missing:
            raise ConfigurationError(
                f"tasks.modes.{higher}.required_metadata must include {lower} fields: "
                + ", ".join(missing)
            )
    default_mode = task_policy.get("default_mode", "STANDARD")
    if default_mode not in MODE_ORDER:
        raise ConfigurationError("tasks.default_mode must be LIGHT, STANDARD, or STRICT")

    raw_defaults = task_policy.get("metadata_defaults", DEFAULT_TASK_METADATA_DEFAULTS)
    if not isinstance(raw_defaults, dict):
        raise ConfigurationError("tasks.metadata_defaults must be an object")
    effective_defaults = {
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
            f"{field_name}.{pattern_id}.regex contains a potentially unsafe quantified alternation"
        )
    try:
        compiled = re.compile(expression)
    except re.error as exc:
        raise ConfigurationError(f"{field_name}.{pattern_id}.regex is invalid: {exc}") from exc
    return pattern_id, compiled


def configure_secret_scan(policy: dict) -> None:
    trust = policy.get("trust")
    if not isinstance(trust, dict):
        raise ConfigurationError("registry policy trust must be an object")
    for field_name in ("allowed_classifications", "allowed_provenance"):
        allowed = trust.get(field_name)
        if not isinstance(allowed, list) or not allowed or any(
            not isinstance(item, str) or not item for item in allowed
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
            f"secret_scan.additional_patterns exceeds {MAX_ADDITIONAL_SECRET_PATTERNS} entries"
        )
    mandatory = dict(BUILTIN_SECRET_PATTERNS)
    legacy_additional: list[tuple[str, re.Pattern[str]]] = []
    seen_policy_ids: set[str] = set()
    for item in builtin:
        pattern_id, compiled = compile_secret_pattern(item, "trust.forbidden_patterns")
        if pattern_id in seen_policy_ids:
            raise ConfigurationError(
                f"duplicate trust.forbidden_patterns pattern id: {pattern_id}"
            )
        seen_policy_ids.add(pattern_id)
        if pattern_id in mandatory:
            if compiled.pattern != mandatory[pattern_id]:
                raise ConfigurationError(
                    f"mandatory built-in secret pattern must not be changed: {pattern_id}"
                )
        else:
            legacy_additional.append((pattern_id, compiled))
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
    compiled_patterns = [
        compile_secret_pattern(
            {"id": pattern_id, "regex": expression}, "built-in secret patterns"
        )
        for pattern_id, expression in BUILTIN_SECRET_PATTERNS
    ]
    compiled_patterns.extend(legacy_additional)
    compiled_patterns.extend(
        compile_secret_pattern(item, "secret_scan.additional_patterns")
        for item in additional
    )
    identifiers = [pattern_id for pattern_id, _ in compiled_patterns]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigurationError("secret scan pattern ids must be unique")
    policy["_compiled_secret_patterns"] = compiled_patterns


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
    gate = policy.get("architecture_gate")
    if not isinstance(gate, dict):
        raise ConfigurationError("registry policy architecture_gate must be an object")
    if not isinstance(gate.get("enabled", True), bool):
        raise ConfigurationError("architecture_gate.enabled must be a boolean")
    branch_template = gate.get("branch_history_template")
    if not isinstance(branch_template, str):
        raise ConfigurationError("architecture_gate.branch_history_template must be a string")
    without_placeholder = branch_template.replace("{branch_slug}", "", 1)
    if branch_template.count("{branch_slug}") != 1 or "{" in without_placeholder or "}" in without_placeholder:
        raise ConfigurationError(
            "architecture_gate.branch_history_template must contain exactly one {branch_slug} placeholder"
        )
    gate["_effective_branch_history_template"] = normalize_repo_relative_glob(
        branch_template, "architecture_gate.branch_history_template"
    )
    no_registry_impacts = gate.get("no_registry_impacts")
    if not isinstance(no_registry_impacts, list) or any(
        not isinstance(item, str) or not item for item in no_registry_impacts
    ):
        raise ConfigurationError("architecture_gate.no_registry_impacts must be a string array")
    allowed_impacts = policy["tasks"].get("architecture_impacts")
    if not isinstance(allowed_impacts, list) or any(
        not isinstance(item, str) or not item for item in allowed_impacts
    ):
        raise ConfigurationError("tasks.architecture_impacts must be a string array")
    unknown = sorted(set(no_registry_impacts) - set(allowed_impacts))
    if unknown:
        raise ConfigurationError(
            "architecture_gate.no_registry_impacts contains unknown values: " + ", ".join(unknown)
        )
    legacy_sensitive = gate.get("sensitive_globs")
    default_sensitive = gate.get("default_sensitive_globs", legacy_sensitive)
    if default_sensitive is None:
        raise ConfigurationError(
            "architecture_gate.default_sensitive_globs (or legacy sensitive_globs) is required"
        )
    gate["_effective_sensitive_globs"] = [
        *normalize_glob_list(default_sensitive, "architecture_gate.default_sensitive_globs"),
        *normalize_glob_list(
            gate.get("additional_sensitive_globs", []),
            "architecture_gate.additional_sensitive_globs",
        ),
    ]
    gate["_effective_ignored_globs"] = normalize_glob_list(
        gate.get("ignored_globs", []), "architecture_gate.ignored_globs"
    )
    escalation = gate.get("mode_escalation", {})
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
    gate["_effective_mode_escalation"] = escalation
    return policy
