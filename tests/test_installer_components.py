#!/usr/bin/env python3
"""Host registry, language preference, and interactive installer tests."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PACKAGE_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install as installer  # noqa: E402
from registry.hosts import HOSTS, expand_host_ids, validate_registry  # noqa: E402
from registry.preferences import (  # noqa: E402
    load_preferences,
    validate_language_tag,
    with_language,
)


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class HostRegistryTests(unittest.TestCase):
    def test_registry_is_valid_unique_and_does_not_model_all(self) -> None:
        validate_registry()
        self.assertEqual({"codex", "claude-code"}, {host.id for host in HOSTS})
        self.assertNotIn("all", {host.id for host in HOSTS})
        self.assertEqual(len(HOSTS), len({host.destination for host in HOSTS}))

    def test_all_duplicates_and_selection_order_normalize_to_registry_order(self) -> None:
        expected = [host.id for host in HOSTS]
        self.assertEqual(expected, [host.id for host in expand_host_ids(["all"])])
        selected = expand_host_ids(["claude-code", "codex", "claude-code"])
        self.assertEqual(expected, [host.id for host in selected])

    def test_unknown_host_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown host"):
            expand_host_ids(["untrusted-host"])


class LanguagePreferenceTests(unittest.TestCase):
    def test_language_tags_are_generic_and_normalized(self) -> None:
        expected = {
            "vi": "vi",
            "EN": "en",
            "ja": "ja",
            "ko": "ko",
            "th": "th",
            "fr": "fr",
            "de": "de",
            "PT-br": "pt-BR",
            "zh-hant-tw": "zh-Hant-TW",
        }
        for raw, normalized in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalized, validate_language_tag(raw))

    def test_unsafe_language_values_are_rejected(self) -> None:
        for value in ("/tmp/x", "vi;touch-x", "vi\nen", '{"x":1}', "", "x" * 40):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_language_tag(value)

    def test_explicit_update_preserves_unrelated_keys(self) -> None:
        existing = {
            "future": {"enabled": True},
            "language": {
                "responses": "fr",
                "generated_documents": "fr",
                "preserve_existing_document_language": False,
                "future_language_key": "data-only",
            },
        }
        updated = with_language(existing, "pt-br")
        self.assertEqual({"enabled": True}, updated["future"])
        self.assertEqual("data-only", updated["language"]["future_language_key"])
        self.assertFalse(updated["language"]["preserve_existing_document_language"])
        self.assertEqual("pt-BR", updated["language"]["responses"])
        self.assertEqual("pt-BR", updated["language"]["generated_documents"])

    def test_malformed_preferences_fail_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "preferences.json"
            path.write_text('{"language":{"responses":[]}}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid language tag"):
                load_preferences(path)

    def test_interactive_shortcuts_and_custom_tag(self) -> None:
        cases = (("1", "vi"), ("2", "en"), ("4", "pt-BR"))
        for selection, expected in cases:
            responses = [selection] if selection != "4" else [selection, "pt-br"]
            with self.subTest(selection=selection), mock.patch(
                "builtins.input", side_effect=responses
            ):
                self.assertEqual(expected, installer.interactive_language())


class InteractiveInstallerTests(unittest.TestCase):
    def test_interactive_multi_select_writes_one_shared_preference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="interactive-installer-test.") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (root / "README.md").write_text("# test\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
            stdin = TTYBuffer("1,1,2\n4\nja\n")
            stdout = TTYBuffer()
            with mock.patch.object(installer.sys, "argv", ["install.py", "--project", str(root)]), mock.patch.object(
                installer.sys, "stdin", stdin
            ), mock.patch.object(installer.sys, "stdout", stdout):
                self.assertEqual(0, installer.main())
            self.assertTrue((root / ".agents/skills/project-agent-workflow/SKILL.md").is_file())
            self.assertTrue((root / ".claude/skills/project-agent-workflow/SKILL.md").is_file())
            preferences = json.loads((root / ".agents/preferences.json").read_text(encoding="utf-8"))
            self.assertEqual("ja", preferences["language"]["responses"])
            self.assertEqual("ja", preferences["language"]["generated_documents"])


if __name__ == "__main__":
    unittest.main()
