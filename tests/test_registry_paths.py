#!/usr/bin/env python3
"""Segment-aware repository glob contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from registry.paths import repo_glob_match  # noqa: E402


class RepositoryGlobTests(unittest.TestCase):
    def test_single_star_stays_within_one_segment(self) -> None:
        self.assertTrue(repo_glob_match("config/*", "config/dev.yml"))
        self.assertFalse(repo_glob_match("config/*", "config/payment/prod.yml"))

    def test_double_star_crosses_segments(self) -> None:
        self.assertTrue(repo_glob_match("config/**", "config/dev.yml"))
        self.assertTrue(repo_glob_match("config/**", "config/payment/prod.yml"))

    def test_fixed_and_recursive_middle_segments(self) -> None:
        self.assertTrue(repo_glob_match("src/*/domain/**", "src/app/domain/model.py"))
        self.assertFalse(
            repo_glob_match("src/*/domain/**", "src/platform/app/domain/model.py")
        )
        self.assertTrue(
            repo_glob_match("src/**/domain/**", "src/platform/app/domain/model.py")
        )

    def test_root_and_recursive_suffix_patterns_differ(self) -> None:
        self.assertTrue(repo_glob_match("*.tf", "main.tf"))
        self.assertFalse(repo_glob_match("*.tf", "infra/main.tf"))
        self.assertTrue(repo_glob_match("**/*.tf", "main.tf"))
        self.assertTrue(repo_glob_match("**/*.tf", "infra/prod/main.tf"))

    def test_migrations_pattern_is_root_scoped(self) -> None:
        self.assertTrue(repo_glob_match("migrations/**", "migrations/001.sql"))
        self.assertFalse(
            repo_glob_match("migrations/**", "service/migrations/001.sql")
        )

    def test_question_mark_and_windows_separators(self) -> None:
        self.assertTrue(repo_glob_match("config/?.yml", r"config\a.yml"))
        self.assertFalse(repo_glob_match("config/?.yml", r"config\ab.yml"))
        self.assertTrue(
            repo_glob_match("src/**/security/**", r"src\app\security\auth.py")
        )


if __name__ == "__main__":
    unittest.main()
