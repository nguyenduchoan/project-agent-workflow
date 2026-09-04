#!/usr/bin/env python3
"""Validate a parent-tracked, project-local .agents registry."""

from __future__ import annotations

import argparse
import fnmatch
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from urllib.parse import unquote, urlparse


EXIT_VALIDATION = 1
EXIT_CONFIGURATION = 64
METADATA_RE = re.compile(r"^\s*-\s+([^:\n]+):\s*(.*?)\s*$")
INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+(?:\s+['\"(].*)?)$", re.MULTILINE)

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


def run_git(
    repo: Path,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
    binary: bool = False,
) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
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


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def relative(agents_root: Path, path: Path) -> str:
    return path.relative_to(agents_root).as_posix()


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
        if root.is_dir():
            result.extend(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    return sorted(set(result))


def scan_symlinks(agents_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for directory in ("tasks", "architecture", "reviews", "policies"):
        root = agents_root / directory
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


def scan_tasks(agents_root: Path, project_root: Path, policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    task_policy = policy["tasks"]
    paths = list((agents_root / "tasks" / "active").glob("*.md"))
    paths.extend((agents_root / "tasks" / "history").glob("*/*.md"))
    for path in sorted(paths):
        rel_path = relative(agents_root, path)
        metadata = parse_metadata(path.read_text(encoding="utf-8"))
        values: dict[str, str] = {}
        for label in task_policy["required_metadata"]:
            value = metadata_value(metadata, label)
            if value is None or not value.strip():
                findings.append(Finding("task-metadata", rel_path, f"missing metadata: {label}"))
            else:
                values[label] = value

        task_id = values.get("ID")
        if task_id and task_id != path.stem:
            findings.append(
                Finding("task-id", rel_path, f"ID '{task_id}' does not match filename '{path.stem}'")
            )

        state = enum_value(values.get("Status", ""))
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

        gate = enum_value(values.get("Delivery gate", ""))
        if gate and gate not in task_policy["delivery_gates"]:
            findings.append(Finding("delivery-gate", rel_path, f"unknown delivery gate: {gate}"))

        impact = enum_value(values.get("Architecture impact", ""))
        if impact and impact not in task_policy["architecture_impacts"]:
            findings.append(
                Finding("architecture-impact", rel_path, f"unknown architecture impact: {impact}")
            )

        source_commit = values.get("Source commit")
        if source_commit:
            validate_source_commit(project_root, source_commit, rel_path, findings)
        created = values.get("Created")
        updated = values.get("Updated")
        if created:
            validate_iso_date(created, "Created", rel_path, findings)
        if updated:
            validate_iso_date(updated, "Updated", rel_path, findings)
        if created and updated:
            try:
                if date.fromisoformat(clean(updated)) < date.fromisoformat(clean(created)):
                    findings.append(
                        Finding("task-date-order", rel_path, "Updated must be on or after Created")
                    )
            except ValueError:
                pass
    return findings


def validate_iso_date(raw: str, label: str, rel_path: str, findings: list[Finding]) -> None:
    try:
        parsed = date.fromisoformat(clean(raw))
    except ValueError:
        findings.append(Finding("record-date", rel_path, f"{label} must be an ISO date: {raw}"))
        return
    if parsed > date.today():
        findings.append(Finding("record-date", rel_path, f"{label} must not be in the future: {raw}"))


def validate_source_commit(
    project_root: Path, raw: str, rel_path: str, findings: list[Finding]
) -> None:
    value = clean(raw).lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", value):
        findings.append(
            Finding("record-source-commit", rel_path, "Source commit must be a 7-40 character commit ID")
        )
        return
    result = run_git(
        project_root,
        ["rev-parse", "--verify", f"{value}^{{commit}}"],
        allow_failure=True,
    )
    assert isinstance(result, str)
    if not result.strip():
        findings.append(Finding("record-source-commit", rel_path, f"commit does not resolve: {value}"))


def scan_records(agents_root: Path, project_root: Path, policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    record_policy = policy["records"]
    rules = (
        (
            sorted((agents_root / "architecture" / "branches").glob("*.md")),
            record_policy["branch_required_metadata"],
            "branch",
        ),
        (
            [
                path
                for path in sorted((agents_root / "architecture" / "changes").glob("*.md"))
                if path.name != "index.md"
            ],
            record_policy["change_required_metadata"],
            "change",
        ),
    )
    for paths, required, kind in rules:
        for path in paths:
            rel_path = relative(agents_root, path)
            metadata = parse_metadata(path.read_text(encoding="utf-8"))
            for label in required:
                if not metadata_value(metadata, label):
                    findings.append(Finding("record-metadata", rel_path, f"missing metadata: {label}"))
            source_commit = metadata.get("Source commit")
            if source_commit:
                validate_source_commit(project_root, source_commit, rel_path, findings)
            for label in ("Created", "Updated", "Date", "Verified at"):
                if metadata.get(label):
                    validate_iso_date(metadata[label], label, rel_path, findings)
            if kind == "change":
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
                    findings.append(Finding("record-status", rel_path, f"unknown change status: {status}"))
                gate = enum_value(metadata.get("Delivery gate", ""))
                if gate and gate not in policy["tasks"]["delivery_gates"]:
                    findings.append(Finding("delivery-gate", rel_path, f"unknown delivery gate: {gate}"))
            else:
                slug = clean(metadata.get("Slug", ""))
                if slug and slug != path.stem:
                    findings.append(
                        Finding(
                            "record-slug",
                            rel_path,
                            f"Slug '{slug}' does not match filename '{path.stem}'",
                        )
                    )
                stale_after = clean(metadata.get("Stale after days", ""))
                if stale_after:
                    try:
                        if int(stale_after) <= 0:
                            raise ValueError
                    except ValueError:
                        findings.append(
                            Finding(
                                "record-stale-threshold",
                                rel_path,
                                "Stale after days must be a positive integer",
                            )
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
    patterns = [(item["id"], re.compile(item["regex"])) for item in trust["forbidden_patterns"]]
    for path in files:
        rel_path = relative(agents_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if registry_record(rel_path):
            metadata = parse_metadata(text)
            classification = clean(metadata.get("Data classification", "")).lower()
            provenance = clean(metadata.get("Provenance", "")).lower()
            executable = clean(metadata.get("Executable", "")).lower()
            if classification not in trust["allowed_classifications"]:
                findings.append(Finding("data-classification", rel_path, "missing or invalid classification"))
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
        if path.suffix.lower() not in {".md", ".mb"}:
            continue
        rel_path = relative(agents_root, path)
        text = path.read_text(encoding="utf-8")
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
    included = [path for path in files if path.suffix.lower() in {".md", ".mb", ".yml", ".yaml"}]
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
    tokens = [token.decode("utf-8") for token in raw.split(b"\0") if token]
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
    if base_ref.startswith("-") or any(character.isspace() for character in base_ref):
        raise ConfigurationError(f"unsafe base ref: {base_ref!r}")
    run_git(project_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    result: dict[str, set[str]] = {}
    for arguments in (
        ["diff", "--name-status", "-z", f"{base_ref}...HEAD", "--"],
        ["diff", "--name-status", "-z", "--"],
        ["diff", "--cached", "--name-status", "-z", "--"],
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
            result.setdefault(raw_path.decode("utf-8"), set()).add("A")
    return result


def covered_by_task(path: str, affected_paths: str) -> bool:
    for raw in affected_paths.split(","):
        pattern = clean(raw)
        if not pattern or pattern in {"*", "**", "**/*", ".", "/"}:
            continue
        if path == pattern or fnmatch.fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"):
            return True
    return False


def scan_architecture_gate(
    project_root: Path,
    agents_root: Path,
    base_ref: str,
    policy: dict,
    existing_findings: list[Finding],
) -> tuple[list[Finding], list[str]]:
    changes = changed_entries(project_root, base_ref)
    gate = policy["architecture_gate"]
    sensitive = sorted(
        path
        for path in changes
        if not path.startswith(".agents/") and matches(path, gate["sensitive_globs"])
    )
    if not sensitive:
        return [], ["no architecture-sensitive project path changed"]

    branch_output = run_git(project_root, ["branch", "--show-current"])
    assert isinstance(branch_output, str)
    branch = branch_output.strip()
    if not branch:
        raise ConfigurationError("detached HEAD cannot be mapped to a branch architecture record")

    branch_rel = gate["branch_history_template"].format(branch_slug=branch.replace("/", "-"))
    branch_repo_path = f".agents/{branch_rel}"
    branch_path = agents_root / branch_rel
    branch_errors = [finding for finding in existing_findings if finding.path == branch_rel]
    if (
        changes.get(branch_repo_path, set()).intersection({"A", "M"})
        and branch_path.is_file()
        and not branch_errors
    ):
        return [], [f"validated branch architecture delta: {branch_repo_path}"]

    uncovered = set(sensitive)
    for repo_path, statuses in sorted(changes.items()):
        if not fnmatch.fnmatchcase(repo_path, ".agents/tasks/active/*.md"):
            continue
        if not statuses.intersection({"A", "M"}):
            continue
        rel_path = repo_path[len(".agents/") :]
        task_path = agents_root / rel_path
        if not task_path.is_file() or any(item.path == rel_path for item in existing_findings):
            continue
        metadata = parse_metadata(task_path.read_text(encoding="utf-8"))
        task_branch = clean(metadata.get("Branch", ""))
        impact = enum_value(metadata_value(metadata, "Architecture impact") or "")
        affected = clean(metadata.get("Affected paths", ""))
        if task_branch != branch or impact not in gate["no_registry_impacts"]:
            continue
        uncovered = {path for path in uncovered if not covered_by_task(path, affected)}

    if not uncovered:
        return [], ["architecture-sensitive paths are covered by changed no-impact task records"]
    return [
        Finding(
            "architecture-registry-missing",
            branch_repo_path,
            "architecture-sensitive paths lack a changed branch record or scoped no-impact task: "
            + ", ".join(sorted(uncovered)),
        )
    ], []


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    notes: list[str] = []
    try:
        project_root = resolve_git_root(args.project.resolve())
        agents_root = project_root / ".agents"
        if not agents_root.is_dir():
            raise ConfigurationError(f"missing project registry: {agents_root}")
        if (agents_root / ".git").exists():
            raise ConfigurationError(
                "nested .agents Git mode is project-specific; use that project's native validator"
            )
        policy_path = agents_root / "policies" / "registry-policy.json"
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"invalid registry policy: {exc}") from exc
        if policy.get("schema_version") != 1:
            raise ConfigurationError("unsupported registry policy schema_version")

        files = data_files(agents_root)
        findings: list[Finding] = []
        findings.extend(scan_symlinks(agents_root))
        findings.extend(scan_tasks(agents_root, project_root, policy))
        findings.extend(scan_records(agents_root, project_root, policy))
        findings.extend(scan_trust_and_secrets(agents_root, files, policy))
        findings.extend(scan_links(agents_root, project_root, files))
        findings.extend(scan_context_budget(agents_root, files, policy))
        if args.base_ref:
            gate_findings, gate_notes = scan_architecture_gate(
                project_root, agents_root, args.base_ref, policy, findings
            )
            findings.extend(gate_findings)
            notes.extend(gate_notes)
        findings = sorted(set(findings))
    except ConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION

    if args.json_output:
        print(
            json.dumps(
                {
                    "errors": len(findings),
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
        for note in notes:
            print(f"INFO: {note}")
        print(f"Registry validation: {len(findings)} error(s).")
    return EXIT_VALIDATION if findings else 0


if __name__ == "__main__":
    sys.exit(main())
