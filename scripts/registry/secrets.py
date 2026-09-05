"""Registry trust metadata and secret-pattern scanning."""

from __future__ import annotations

from pathlib import Path

from registry.findings import Finding
from registry.paths import clean, matches, relative
from registry.records import parse_metadata


REGISTRY_TEXT_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}


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
                findings.append(Finding("registry-encoding", rel_path, "registry text must be valid UTF-8"))
            continue
        except OSError as exc:
            findings.append(Finding("registry-read", rel_path, f"registry data could not be read: {exc}"))
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
                findings.append(Finding("data-classification", rel_path, "missing or invalid classification"))
            if provenance not in trust["allowed_provenance"]:
                findings.append(Finding("provenance", rel_path, "missing or invalid provenance"))
            if executable != trust["required_executable"]:
                findings.append(Finding("executable", rel_path, "Executable must be false"))
        for pattern_id, pattern in patterns:
            if pattern.search(text):
                findings.append(Finding("sensitive-data", rel_path, f"matched forbidden pattern: {pattern_id}"))
    return findings
