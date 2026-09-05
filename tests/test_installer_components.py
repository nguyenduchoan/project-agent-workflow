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
        cases = (("1", "vi"), ("2", "en"), ("3", "en"), ("4", "pt-BR"))
        for selection, expected in cases:
            responses = [selection] if selection != "4" else [selection, "pt-br"]
            output = TTYBuffer()
            with self.subTest(selection=selection), mock.patch(
                "builtins.input", side_effect=responses
            ), mock.patch.object(installer.sys, "stdout", output):
                self.assertEqual(expected, installer.interactive_language())
            if selection == "3":
                self.assertIn("[3] Default / English (en)", output.getvalue())


class PermissionValidationTests(unittest.TestCase):
    def test_runtime_code_rejects_group_or_world_writable_modes(self) -> None:
        cases = (
            ("runtime.sh", 0o755, ((0o755, True), (0o775, False), (0o777, False))),
            ("runtime.py", 0o644, ((0o644, True), (0o664, False), (0o666, False))),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, source_mode, modes in cases:
                source = root / f"source-{name}"
                destination = root / name
                source.write_text("runtime\n", encoding="utf-8")
                destination.write_text("runtime\n", encoding="utf-8")
                source.chmod(source_mode)
                for mode, accepted in modes:
                    with self.subTest(name=name, mode=oct(mode)):
                        destination.chmod(mode)
                        if accepted:
                            installer.check_file(source, destination, root)
                        else:
                            with self.assertRaisesRegex(ValueError, "group/world-writable"):
                                installer.check_file(source, destination, root)


class InteractiveInstallerTests(unittest.TestCase):
    def test_write_phase_rechecks_content_before_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="installer-race-test.") as temporary:
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

            with mock.patch.object(
                installer.sys,
                "argv",
                ["install.py", "--project", str(root), "--host", "codex"],
            ):
                self.assertEqual(0, installer.main())

            managed_file = root.resolve() / ".agents/skills/project-agent-workflow/SKILL.md"
            claude_root = root.resolve() / ".claude/skills/project-agent-workflow"
            changed_content = "changed after preflight\n"
            original_describe_or_write = installer.describe_or_write
            mutation_applied = False

            def mutate_before_write(
                source: Path, destination: Path, repo: Path, dry_run: bool
            ) -> None:
                nonlocal mutation_applied
                if destination == managed_file and not mutation_applied:
                    managed_file.write_text(changed_content, encoding="utf-8")
                    mutation_applied = True
                original_describe_or_write(source, destination, repo, dry_run)

            stderr = io.StringIO()
            with mock.patch.object(
                installer.sys,
                "argv",
                [
                    "install.py",
                    "--project",
                    str(root),
                    "--host",
                    "codex",
                    "--host",
                    "claude-code",
                ],
            ), mock.patch.object(
                installer, "describe_or_write", side_effect=mutate_before_write
            ), mock.patch.object(installer.sys, "stderr", stderr):
                self.assertEqual(1, installer.main())

            self.assertTrue(mutation_applied)
            self.assertEqual(changed_content, managed_file.read_text(encoding="utf-8"))
            self.assertFalse(claude_root.exists())
            self.assertIn("managed destination changed during install", stderr.getvalue())

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
