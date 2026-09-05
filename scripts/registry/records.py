"""Parse and validate task records plus common registry files."""

from __future__ import annotations

import posixpath
import re
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from registry.findings import ConfigurationError, Finding, TaskRecord, TaskRelevance
from registry.git import current_git_branch, current_git_head, git_commit, run_git
from registry.paths import (
    clean,
    has_symlink_component,
    matches,
    normalize_repo_relative_glob,
    relative,
    repo_glob_match,
)
from registry.policy import MODE_ORDER


METADATA_RE = re.compile(r"^\s*-\s+([^:\n]+):\s*(.*?)\s*$")
INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+(?:\s+['\"(].*)?)$", re.MULTILINE)
METADATA_ALIASES = {
    "Status": ("Status", "Trạng thái"),
    "Created": ("Created", "Ngày tạo"),
    "Updated": ("Updated", "Cập nhật gần nhất"),
    "Architecture impact": ("Architecture impact", "Phân loại"),
}


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


def read_registry_text(path: Path, agents_root: Path, findings: list[Finding]) -> str | None:
    rel_path = relative(agents_root, path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError:
        findings.append(Finding("registry-encoding", rel_path, "registry text must be valid UTF-8"))
    except OSError as exc:
        findings.append(
            Finding("registry-read", rel_path, f"registry text could not be read: {exc}")
        )
    return None


def validate_affected_paths(raw: str, rel_path: str, findings: list[Finding]) -> tuple[str, ...]:
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


def scan_symlinks(agents_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for directory in ("tasks", "architecture", "reviews", "policies"):
        root = agents_root / directory
        if root.is_symlink():
            findings.append(
                Finding("symlink", relative(agents_root, root), "registry data paths must not be symlinks")
            )
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink():
                findings.append(
                    Finding("symlink", relative(agents_root, path), "registry data paths must not be symlinks")
                )
    return findings


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
        findings.append(Finding("record-date", rel_path, f"{label} must not be in the future: {raw}"))
        return None
    return parsed


def compute_record_age(verified_at: date, validation_date: date) -> int:
    return (validation_date - verified_at).days


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
        findings.append(Finding(code, rel_path, f"{label} must be a 7-40 character commit ID"))
        return None
    if project_root is None:
        return value
    resolved = git_commit(project_root, value)
    if not resolved:
        findings.append(Finding(code, rel_path, f"{label} does not resolve to a commit: {value}"))
        return None
    return resolved


def validate_source_commit(
    project_root: Path | None, raw: str, rel_path: str, findings: list[Finding]
) -> None:
    validate_commit_id(
        project_root, raw, "Source commit", "record-source-commit", rel_path, findings
    )


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
        findings.append(Finding("record-base-ref", rel_path, f"{label} does not resolve to a commit: {value}"))
        return None
    return resolved


def validate_merge_base(
    project_root: Path | None,
    base_ref: str,
    head_commit: str,
    raw_merge_base: str,
    rel_path: str,
    findings: list[Finding],
) -> None:
    merge_base = validate_commit_id(
        project_root, raw_merge_base, "Merge-base", "record-merge-base", rel_path, findings
    )
    if project_root is None or merge_base is None:
        return
    output = run_git(project_root, ["merge-base", base_ref, head_commit], allow_failure=True)
    assert isinstance(output, str)
    expected = output.strip()
    if not expected:
        findings.append(
            Finding("record-merge-base", rel_path, "Merge-base cannot be computed from Base ref and Current head")
        )
    elif merge_base != expected:
        findings.append(
            Finding("record-merge-base", rel_path, f"Merge-base is {merge_base}, expected {expected}")
        )


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
        len(reference.parts) == 3 and reference.parts[:2] == ("tasks", "active")
        or len(reference.parts) == 4 and reference.parts[:2] == ("tasks", "history")
    )
    if not allowed or reference.suffix != ".md":
        return [], f"Related task must point inside tasks/active or tasks/history: {raw}"
    return [agents_root / reference], None


def resolve_related_task_reference(agents_root: Path, raw: str) -> tuple[Path | None, str | None]:
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


def validate_related_task(
    agents_root: Path, raw: str, rel_path: str, findings: list[Finding]
) -> None:
    _path, error = resolve_related_task_reference(agents_root, raw)
    if error:
        findings.append(Finding("record-related-task", rel_path, error))


def architecture_reference_values(raw: str) -> tuple[str, ...]:
    values = [item.strip().strip("`").strip() for item in raw.strip().split(",")]
    values = [value for value in values if value]
    if not values or (len(values) == 1 and values[0].lower() in {"none", "n/a", "not-applicable"}):
        return ()
    return tuple(values)


def architecture_record_candidates(agents_root: Path, raw: str) -> tuple[list[Path], str | None]:
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
    allowed = len(reference.parts) == 3 and reference.parts[:2] in {
        ("architecture", "branches"), ("architecture", "changes")
    }
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
    agents_root: Path, raw: str, rel_path: str, findings: list[Finding]
) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for reference in architecture_reference_values(raw):
        path, error = resolve_architecture_record_reference(agents_root, reference)
        if error:
            findings.append(Finding("record-related-architecture", rel_path, error))
        elif path is not None:
            resolved.append(path)
    return tuple(resolved)


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
        path for path in (agents_root / "tasks" / "active").glob("*.md")
        if not has_symlink_component(agents_root, path)
    ]
    paths.extend(
        path for path in (agents_root / "tasks" / "history").glob("*/*.md")
        if not has_symlink_component(agents_root, path)
    )
    for path in sorted(paths):
        rel_path = relative(agents_root, path)
        is_active = repo_glob_match("tasks/active/*.md", rel_path)
        text = read_registry_text(path, agents_root, findings)
        if text is None:
            continue
        metadata = parse_metadata(text)
        affected_paths = validate_affected_paths(metadata.get("Affected paths", ""), rel_path, findings)
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
        if raw_mode is not None:
            mode = clean(raw_mode).upper()
            if mode not in MODE_ORDER:
                findings.append(Finding("task-mode", rel_path, f"Mode must be LIGHT, STANDARD, or STRICT: {raw_mode}"))
                mode = task_policy.get("default_mode", "STANDARD")
            required_metadata = task_policy["_effective_modes"][mode]
        else:
            required_metadata = task_policy.get("required_metadata", task_policy["_effective_modes"]["STANDARD"])
        for label in required_metadata:
            value = metadata_value(metadata, label)
            if value is None or not value.strip():
                findings.append(Finding("task-metadata", rel_path, f"missing metadata: {label}"))
        task_id = metadata_value(metadata, "ID")
        if task_id and task_id != path.stem:
            findings.append(Finding("task-id", rel_path, f"ID '{task_id}' does not match filename '{path.stem}'"))
        state = enum_value(metadata_value(metadata, "Status") or "")
        if state and state not in task_policy["canonical_states"]:
            findings.append(Finding("task-state", rel_path, f"unknown canonical state: {state}"))
        for pattern, allowed in task_policy["directory_states"].items():
            if repo_glob_match(pattern, rel_path) and state and state not in allowed:
                findings.append(Finding("task-state-directory", rel_path, f"state '{state}' is invalid here; allowed: {', '.join(allowed)}"))
        gate = enum_value(metadata.get("Delivery gate", ""))
        if gate and gate not in task_policy["delivery_gates"]:
            findings.append(Finding("delivery-gate", rel_path, f"unknown delivery gate: {gate}"))
        impact = enum_value(metadata_value(metadata, "Architecture impact") or "")
        if impact and impact not in task_policy["architecture_impacts"]:
            findings.append(Finding("architecture-impact", rel_path, f"unknown architecture impact: {impact}"))
        source_commit = metadata.get("Source commit")
        if source_commit:
            validate_source_commit(project_root, source_commit, rel_path, findings)
        legacy_base = metadata.get("Base ref / merge-base")
        if legacy_base:
            validate_base_ref(project_root, legacy_base, rel_path, findings, label="Base ref / merge-base")
        base_ref = metadata.get("Base ref")
        merge_base = metadata.get("Merge-base")
        current_head = metadata.get("Current head")
        resolved_base = validate_base_ref(project_root, base_ref, rel_path, findings) if base_ref else None
        resolved_head = validate_commit_id(project_root, current_head, "Current head", "record-current-head", rel_path, findings) if current_head else None
        if merge_base and resolved_base and resolved_head:
            validate_merge_base(project_root, clean(base_ref or ""), resolved_head, merge_base, rel_path, findings)
        elif merge_base:
            validate_commit_id(project_root, merge_base, "Merge-base", "record-merge-base", rel_path, findings)
        if metadata.get("Related task"):
            validate_related_task(agents_root, metadata["Related task"], rel_path, findings)
        architecture_links = validate_related_architecture_records(
            agents_root, metadata.get("Related architecture records", ""), rel_path, findings
        )
        if is_active and relevance is TaskRelevance.DETACHED and metadata.get("Branch"):
            warnings.append(f"{rel_path}: checkout-relative Branch and Current head checks skipped because HEAD is detached")
        if relevance is TaskRelevance.CURRENT and project_root is not None and resolved_head:
            checkout_head = current_git_head(project_root)
            if resolved_head != checkout_head:
                findings.append(Finding("record-current-head", rel_path, f"Current head is {resolved_head}, expected {checkout_head}"))
        created = metadata_value(metadata, "Created")
        updated = metadata_value(metadata, "Updated")
        if created:
            validate_iso_date(created, "Created", rel_path, findings, validation_date)
        if updated:
            validate_iso_date(updated, "Updated", rel_path, findings, validation_date)
        if created and updated:
            try:
                if date.fromisoformat(clean(updated)) < date.fromisoformat(clean(created)):
                    findings.append(Finding("task-date-order", rel_path, "Updated must be on or after Created"))
            except ValueError:
                pass
        records.append(TaskRecord(path, rel_path, metadata, relevance, architecture_links, affected_paths))
    return findings, records


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
                findings.append(Finding("broken-link", rel_path, f"link target does not exist: {raw}"))
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
            if repo_glob_match(pattern, rel_path):
                limit = configured
                break
        size = path.stat().st_size
        if size > limit:
            findings.append(Finding("context-file", rel_path, f"{size} bytes exceeds {limit}"))
    return findings
