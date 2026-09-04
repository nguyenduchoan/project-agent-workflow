#!/usr/bin/env python3
"""Shared standard-library fixtures for registry validator tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = PACKAGE_ROOT / "scripts" / "validate_registry.py"
PROJECT_TEMPLATE = PACKAGE_ROOT / "assets" / "project-template" / ".agents"


class ValidatorRepo:
    def __init__(self, branch: str = "main") -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="project-agent-validator-test.")
        self.root = Path(self._temporary.name)
        self.git("init", "-q")
        self.git("branch", "-M", branch)
        self.git("config", "user.name", "Validator Test")
        self.git("config", "user.email", "validator-test@example.invalid")
        self.write("README.md", "# Validator fixture\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "initial")
        shutil.copytree(PROJECT_TEMPLATE, self.root / ".agents")

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "ValidatorRepo":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def write(self, relative_path: str, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_validator(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--project", str(self.root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def policy(self) -> dict:
        path = self.root / ".agents" / "policies" / "registry-policy.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write_policy(self, policy: dict) -> None:
        path = self.root / ".agents" / "policies" / "registry-policy.json"
        path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")
