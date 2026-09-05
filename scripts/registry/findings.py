"""Shared validation types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when trusted configuration cannot be validated safely."""


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
