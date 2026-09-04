#!/usr/bin/env python3
"""Registry Markdown scope tests, including the removed .mb typo."""

from __future__ import annotations

import unittest

from validator_test_support import ValidatorRepo


class MarkdownScopeTests(unittest.TestCase):
    def test_markdown_file_links_are_validated(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/reviews/evidence.md", "[missing](not-found.md)\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [broken-link]", result.stdout)

    def test_undocumented_mb_suffix_is_not_treated_as_markdown(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/reviews/legacy.mb", "[missing](not-found.md)\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_invalid_utf8_markdown_is_reported_without_traceback(self) -> None:
        with ValidatorRepo("main") as repo:
            path = repo.root / ".agents/reviews/invalid.md"
            path.write_bytes(b"\xff\xfe")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [registry-encoding]", result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_percent_encoded_nul_link_is_rejected_without_traceback(self) -> None:
        with ValidatorRepo("main") as repo:
            repo.write(".agents/reviews/invalid-link.md", "[bad](bad%00.md)\n")
            result = repo.run_validator(
                "--no-architecture-gate", "--today", "2026-09-04"
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("ERROR [link-target]", result.stdout)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
