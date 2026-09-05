"""Architecture record validation, relevance, staleness, and gate logic."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from registry.findings import (
    ArchitectureRecord,
    ConfigurationError,
    Finding,
    RecordRelevance,
    TaskRecord,
    TaskRelevance,
)
from registry.git import changed_entries, current_git_branch, current_git_head
from registry.paths import clean, has_symlink_component, matches, relative, repo_glob_match
from registry.policy import MODE_ORDER
from registry.records import (
    compute_record_age,
    enum_value,
    metadata_value,
    parse_metadata,
    read_registry_text,
    resolve_related_task_reference,
    validate_affected_paths,
    validate_base_ref,
    validate_commit_id,
    validate_iso_date,
    validate_merge_base,
    validate_source_commit,
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
    if kind == "branch" and checkout_branch and clean(metadata.get("Branch", "")) == checkout_branch:
        return RecordRelevance.CURRENT
    if path in other_links or (
        kind == "branch" and clean(metadata.get("Branch", "")) in other_active_branches
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
        linked for task in task_records if task.relevance is TaskRelevance.CURRENT
        for linked in task.architecture_links
    }
    other_links = {
        linked for task in task_records
        if task.relevance in {TaskRelevance.OTHER_ACTIVE, TaskRelevance.DETACHED}
        for linked in task.architecture_links
    }
    other_active_branches = {
        clean(task.metadata.get("Branch", ""))
        for task in task_records if task.relevance is TaskRelevance.OTHER_ACTIVE
    }
    rules = (
        (
            sorted(
                path for path in (agents_root / "architecture" / "branches").glob("*.md")
                if not has_symlink_component(agents_root, path)
            ),
            record_policy["branch_required_metadata"],
            "branch",
        ),
        (
            [
                path for path in sorted((agents_root / "architecture" / "changes").glob("*.md"))
                if path.name != "index.md" and not has_symlink_component(agents_root, path)
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
            affected_paths = validate_affected_paths(metadata.get("Affected paths", ""), rel_path, findings)
            relevance = classify_architecture_record_relevance(
                path, kind, metadata, checkout_branch, current_links, other_links, other_active_branches
            )
            related_task_path: Path | None = None
            for label in required:
                if not metadata_value(metadata, label):
                    findings.append(Finding("record-metadata", rel_path, f"missing metadata: {label}"))
            source_commit = metadata.get("Source commit")
            if source_commit:
                validate_source_commit(project_root, source_commit, rel_path, findings)
            if metadata.get("Related task"):
                related_task_path, error = resolve_related_task_reference(agents_root, metadata["Related task"])
                if error:
                    findings.append(Finding("record-related-task", rel_path, error))
            if metadata.get("Base ref / merge-base"):
                validate_base_ref(
                    project_root, metadata["Base ref / merge-base"], rel_path,
                    findings, label="Base ref / merge-base"
                )
            parsed_dates: dict[str, date] = {}
            for label in ("Created", "Updated", "Date", "Verified at"):
                if metadata.get(label):
                    parsed = validate_iso_date(metadata[label], label, rel_path, findings, validation_date)
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
                    findings.append(Finding("record-stale-threshold", rel_path, "Stale after days must be a positive integer"))
                    stale_after_days = None
            if stale_after_days is not None and "Verified at" in parsed_dates:
                stale_age_days = compute_record_age(parsed_dates["Verified at"], validation_date)
                if stale_age_days > stale_after_days:
                    message = (
                        f"{relevance.value} architecture record is stale "
                        f"({stale_age_days} days > {stale_after_days} days)"
                    )
                    if relevance in {RecordRelevance.CURRENT, RecordRelevance.LINKED}:
                        findings.append(Finding("record-stale", rel_path, message))
                    else:
                        warnings.append(f"[record-stale] {rel_path}: {message}")
            if kind == "change":
                if metadata.get("Head snapshot"):
                    validate_commit_id(
                        project_root, metadata["Head snapshot"], "Head snapshot",
                        "record-head-snapshot", rel_path, findings
                    )
                record_id = clean(metadata.get("ID", ""))
                if record_id and record_id != path.stem:
                    findings.append(Finding("record-id", rel_path, f"ID '{record_id}' does not match filename '{path.stem}'"))
                status = enum_value(metadata.get("Status", ""))
                if status and status not in record_policy["change_statuses"]:
                    findings.append(Finding("record-status", rel_path, f"unknown change status: {status}"))
                gate = enum_value(metadata.get("Delivery gate", ""))
                if gate and gate not in policy["tasks"]["delivery_gates"]:
                    findings.append(Finding("delivery-gate", rel_path, f"unknown delivery gate: {gate}"))
            else:
                branch = clean(metadata.get("Branch", ""))
                slug = clean(metadata.get("Slug", ""))
                if slug and slug != path.stem:
                    findings.append(Finding("record-slug", rel_path, f"Slug '{slug}' does not match filename '{path.stem}'"))
                expected_slug = branch.replace("/", "-")
                if branch and slug and slug != expected_slug:
                    findings.append(Finding("record-branch-slug", rel_path, f"Slug '{slug}' does not match Branch-derived slug '{expected_slug}'"))
                base_ref = metadata.get("Base ref")
                current_head = metadata.get("Current head")
                merge_base = metadata.get("Merge-base")
                resolved_base = validate_base_ref(project_root, base_ref, rel_path, findings) if base_ref else None
                resolved_head = validate_commit_id(project_root, current_head, "Current head", "record-current-head", rel_path, findings) if current_head else None
                if merge_base and resolved_base and resolved_head:
                    validate_merge_base(project_root, clean(base_ref or ""), resolved_head, merge_base, rel_path, findings)
                elif merge_base:
                    validate_commit_id(project_root, merge_base, "Merge-base", "record-merge-base", rel_path, findings)
                if project_root is not None and branch:
                    if not checkout_branch:
                        warnings.append(f"{rel_path}: Current head comparison skipped because HEAD is detached")
                    elif branch == checkout_branch and resolved_head:
                        checkout_head = current_git_head(project_root)
                        if resolved_head != checkout_head:
                            findings.append(Finding("record-current-head", rel_path, f"Current head is {resolved_head}, expected {checkout_head}"))
            records.append(
                ArchitectureRecord(
                    path, rel_path, kind, metadata, relevance,
                    parsed_dates.get("Verified at"), stale_after_days, stale_age_days,
                    related_task_path, affected_paths,
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
            records_by_path[path] for path in task.architecture_links
            if path in records_by_path and records_by_path[path].kind == "change"
        ]
        impact = enum_value(metadata_value(task.metadata, "Architecture impact") or "")
        if impact == "confirmed" and not linked_changes:
            findings.append(Finding(
                "architecture-change-evidence", task.rel_path,
                "confirmed Architecture impact requires a dedicated architecture change record in Related architecture records",
            ))
        for record in linked_changes:
            if record.related_task_path == task.path:
                continue
            task_id = clean(metadata_value(task.metadata, "ID") or task.path.stem)
            message = f"{record.rel_path} does not link back to task {task_id} through Related task"
            if requires_bidirectional_error(task):
                findings.append(Finding("architecture-link-mismatch", task.rel_path, message))
            else:
                warnings.append(f"[architecture-link-mismatch] {task.rel_path}: legacy task link is not bidirectional: {message}")
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
            findings.append(Finding("architecture-link-mismatch", record.rel_path, message))
        else:
            warnings.append(f"[architecture-link-mismatch] {record.rel_path}: legacy task link is not bidirectional: {message}")
    return findings


def is_architecture_sensitive(path: str, gate: dict) -> bool:
    normalized = path.replace("\\", "/")
    return matches(normalized, gate["_effective_sensitive_globs"]) and not matches(
        normalized, gate["_effective_ignored_globs"]
    )


def covered_by_patterns(path: str, affected_paths: Iterable[str]) -> bool:
    for raw in affected_paths:
        pattern = clean(raw)
        if not pattern or pattern in {"*", "**", "**/*", ".", "/"}:
            continue
        if path == pattern or repo_glob_match(pattern, path):
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


def required_mode_for_path(path: str, gate: dict) -> str:
    escalation = gate["_effective_mode_escalation"]
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
        path for path in changes
        if not path.startswith(".agents/") and is_architecture_sensitive(path, gate)
    )
    if not sensitive:
        return [], ["no architecture-sensitive project path changed"]
    branch = current_git_branch(project_root)
    if not branch:
        raise ConfigurationError("detached HEAD cannot be mapped to a branch architecture record")
    branch_rel = gate["_effective_branch_history_template"].format(branch_slug=branch.replace("/", "-"))
    branch_repo_path = f".agents/{branch_rel}"
    branch_path = agents_root / branch_rel
    invalid_paths = {finding.path for finding in existing_findings}
    records_by_path = {record.path: record for record in architecture_records}
    branch_record = records_by_path.get(branch_path)
    branch_evidence = bool(
        branch_record is not None and branch_record.kind == "branch"
        and branch_record.rel_path not in invalid_paths
        and changes.get(branch_repo_path, set()).intersection({"A", "M"})
    )
    gate_findings: list[Finding] = []
    notes: list[str] = []
    freshness_reported: set[str] = set()
    current_tasks = [
        task for task in task_records
        if task.relevance is TaskRelevance.CURRENT and task.rel_path not in invalid_paths
    ]
    for task in task_records:
        if task.relevance is TaskRelevance.HISTORICAL or task.rel_path in invalid_paths:
            continue
        if task.relevance is TaskRelevance.OTHER_ACTIVE and clean(task.metadata.get("Branch", "")):
            continue
        mode = effective_task_mode(task.metadata, policy)
        if mode != "LIGHT":
            continue
        covered = [path for path in sensitive if covered_by_patterns(path, task.affected_paths)]
        if not covered:
            continue
        required_mode = max((required_mode_for_path(path, gate) for path in covered), key=MODE_ORDER.__getitem__)
        if MODE_ORDER[mode] < MODE_ORDER[required_mode]:
            gate_findings.append(Finding("task-mode-escalation", task.rel_path, f"Mode {mode} must be promoted to {required_mode} for sensitive paths: " + ", ".join(covered)))
    uncovered = set(sensitive)
    used_no_impact = used_branch_evidence = used_change_evidence = False
    for task in current_tasks:
        covered = sorted(path for path in sensitive if covered_by_patterns(path, task.affected_paths))
        if not covered:
            continue
        mode = effective_task_mode(task.metadata, policy)
        if mode is None:
            continue
        required_mode = max((required_mode_for_path(path, gate) for path in covered), key=MODE_ORDER.__getitem__)
        if MODE_ORDER[mode] < MODE_ORDER[required_mode]:
            gate_findings.append(Finding("task-mode-escalation", task.rel_path, f"Mode {mode} must be promoted to {required_mode} for sensitive paths: " + ", ".join(covered)))
            continue
        impact = enum_value(metadata_value(task.metadata, "Architecture impact") or "")
        linked_changes = [
            records_by_path[path] for path in task.architecture_links
            if path in records_by_path and records_by_path[path].kind == "change"
        ]
        usable: set[str] = set()
        wrong_branch: list[str] = []
        for record in linked_changes:
            if record.rel_path in invalid_paths or record.related_task_path != task.path:
                continue
            record_branch = clean(record.metadata.get("Branch", ""))
            if record_branch != branch:
                wrong_branch.append(f"{record.rel_path} ({record_branch or 'missing Branch'})")
                continue
            if record.stale_after_days is None:
                if record.rel_path not in freshness_reported:
                    gate_findings.append(Finding("architecture-evidence-freshness", record.rel_path, "gate-required architecture change evidence must declare a positive Stale after days value"))
                    freshness_reported.add(record.rel_path)
                continue
            if record.verified_at is None or record.stale_age_days is None or record.stale_age_days > record.stale_after_days:
                continue
            usable.update(path for path in covered if covered_by_patterns(path, record.affected_paths))
        requires_change = impact == "confirmed" or mode == "STRICT"
        if requires_change:
            missing = sorted(set(covered) - usable)
            if missing:
                requirement = "STRICT sensitive work" if mode == "STRICT" and impact != "confirmed" else "confirmed Architecture impact"
                message = f"{requirement} requires valid, fresh, linked architecture/changes evidence covering: " + ", ".join(missing)
                if wrong_branch:
                    message += "; linked records do not match current branch: " + ", ".join(wrong_branch)
                gate_findings.append(Finding("architecture-change-evidence", task.rel_path, message))
            if usable:
                uncovered.difference_update(usable)
                used_change_evidence = True
            continue
        if impact in gate["no_registry_impacts"]:
            uncovered.difference_update(covered)
            used_no_impact = True
            continue
        if usable:
            uncovered.difference_update(usable)
            used_change_evidence = True
        if impact in {"possible", "architecture-change"} and branch_evidence and (
            branch_path in task.architecture_links or task.metadata.get("Mode") is None
        ):
            uncovered.difference_update(covered)
            used_branch_evidence = True
    if uncovered:
        gate_findings.append(Finding(
            "architecture-registry-missing", branch_repo_path,
            "architecture-sensitive paths are not covered by valid current-branch task evidence: " + ", ".join(sorted(uncovered)),
        ))
    if used_change_evidence:
        notes.append("validated architecture change evidence for current-branch tasks")
    if used_branch_evidence:
        notes.append(f"validated branch architecture evidence: {branch_repo_path}")
    if used_no_impact:
        notes.append("architecture-sensitive paths are covered by current no-impact task records")
    return gate_findings, notes
