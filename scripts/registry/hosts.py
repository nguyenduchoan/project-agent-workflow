"""Trusted package-owned host registry."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from registry.findings import ConfigurationError


@dataclass(frozen=True)
class HostDefinition:
    id: str
    display_name: str
    owned_root: str
    destination: str


HOSTS = (
    HostDefinition(
        "codex",
        "Codex",
        ".agents",
        ".agents/skills/project-agent-workflow",
    ),
    HostDefinition(
        "claude-code",
        "Claude Code",
        ".claude",
        ".claude/skills/project-agent-workflow",
    ),
)


def host_by_id(host_id: str) -> HostDefinition | None:
    return next((host for host in HOSTS if host.id == host_id), None)


def validate_registry(hosts: tuple[HostDefinition, ...] = HOSTS) -> None:
    seen_ids: set[str] = set()
    seen_destinations: set[str] = set()
    for host in hosts:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", host.id) or host.id == "all":
            raise ConfigurationError(f"invalid trusted host id: {host.id!r}")
        if host.id in seen_ids:
            raise ConfigurationError(f"duplicate trusted host id: {host.id}")
        if not host.display_name.strip():
            raise ConfigurationError(f"trusted host has no display name: {host.id}")
        root = PurePosixPath(host.owned_root)
        destination = PurePosixPath(host.destination)
        if (
            root.is_absolute()
            or destination.is_absolute()
            or ".." in root.parts
            or ".." in destination.parts
            or "." in root.parts
            or "." in destination.parts
            or len(root.parts) != 1
            or not root.parts[0].startswith(".")
        ):
            raise ConfigurationError(f"unsafe trusted host path: {host.id}")
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise ConfigurationError(
                f"trusted host destination escapes owned root: {host.id}"
            ) from exc
        if host.destination in seen_destinations:
            raise ConfigurationError(
                f"duplicate trusted host destination: {host.destination}"
            )
        seen_ids.add(host.id)
        seen_destinations.add(host.destination)


def expand_host_ids(values: list[str]) -> list[HostDefinition]:
    validate_registry()
    selected_ids: set[str] = set()
    for value in values:
        if value == "all":
            selected_ids.update(host.id for host in HOSTS)
        else:
            host = host_by_id(value)
            if host is None:
                raise ValueError(f"unknown host '{value}'")
            selected_ids.add(host.id)
    return [host for host in HOSTS if host.id in selected_ids]
