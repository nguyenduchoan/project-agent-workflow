#!/usr/bin/env python3
"""Host registry, language preference, and interactive installer tests."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assemble_skill import assemble

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PACKAGE_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ensure_parent_tracking as tracking  # noqa: E402
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
    def test_executable_capability_matches_source_without_requiring_exact_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "managed"
            source.write_bytes(b"managed content\n")
            destination.write_bytes(source.read_bytes())
            for source_mode in (0o644, 0o700, 0o650, 0o641):
                source.chmod(source_mode)
                for mode in (0o644, 0o600, 0o700, 0o750, 0o744, 0o654, 0o645):
                    with self.subTest(source_mode=oct(source_mode), mode=oct(mode)):
                        destination.chmod(mode)
                        if source_mode & 0o111 and not os.access(destination, os.X_OK):
                            with self.assertRaisesRegex(ValueError, "not executable"):
                                installer.check_file(source, destination, root)
                        else:
                            installer.check_file(source, destination, root)

    def test_lost_execute_bits_fail_preflight_before_any_mutation(self) -> None:
        for host in HOSTS:
            for relative in ("install.sh", "scripts/install.py"):
                for preferences_exist in (False, True):
                    with self.subTest(
                        host=host.id, relative=relative, preferences_exist=preferences_exist
                    ), tempfile.TemporaryDirectory(prefix="permission-preflight-test.") as temporary:
                        root = Path(temporary).resolve()
                        subprocess.run(["git", "init", "-q", str(root)], check=True)
                        destination = root / host.destination / relative
                        destination.parent.mkdir(parents=True)
                        shutil.copy2(PACKAGE_ROOT / relative, destination)
                        destination.chmod(0o644)
                        preferences = root / ".agents/preferences.json"
                        ignore = root / ".gitignore"
                        if preferences_exist:
                            preferences.parent.mkdir(parents=True, exist_ok=True)
                            preferences.write_text('{"language":{"responses":"fr"}}\n', encoding="utf-8")
                            ignore.write_bytes(b"user-ignore/\n")
                        before = {
                            path.relative_to(root): (path.read_bytes(), path.stat().st_mode)
                            for path in root.rglob("*")
                            if path.is_file() and ".git" not in path.relative_to(root).parts
                        }
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with mock.patch.object(
                            sys, "argv", ["install.py", "--project", str(root), "--host", "all", "--language", "vi"]
                        ), mock.patch.object(sys, "stdout", stdout), mock.patch.object(
                            sys, "stderr", stderr
                        ), mock.patch.object(installer, "describe_or_write") as write:
                            self.assertEqual(1, installer.main())
                        self.assertIn("not executable", stderr.getvalue())
                        write.assert_not_called()
                        after = {
                            path.relative_to(root): (path.read_bytes(), path.stat().st_mode)
                            for path in root.rglob("*")
                            if path.is_file() and ".git" not in path.relative_to(root).parts
                        }
                        self.assertEqual(before, after)

    def test_group_or_other_only_execute_bits_fail_preflight_before_mutation(self) -> None:
        for mode in (0o654, 0o645):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory(
                prefix="permission-execute-capability-test."
            ) as temporary:
                root = Path(temporary).resolve()
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                destination = root / HOSTS[0].destination / "install.sh"
                destination.parent.mkdir(parents=True)
                shutil.copy2(PACKAGE_ROOT / "install.sh", destination)
                destination.chmod(mode)
                preferences = root / ".agents/preferences.json"
                preferences.parent.mkdir(parents=True, exist_ok=True)
                preferences.write_text('{"language":{"responses":"fr"}}\n', encoding="utf-8")
                ignore = root / ".gitignore"
                ignore.write_bytes(b"user-ignore/\n")
                before = {
                    path.relative_to(root): (path.read_bytes(), path.stat().st_mode)
                    for path in root.rglob("*")
                    if path.is_file() and ".git" not in path.relative_to(root).parts
                }
                stderr = io.StringIO()
                with mock.patch.object(
                    sys, "argv", ["install.py", "--project", str(root), "--host", "codex", "--language", "vi"]
                ), mock.patch.object(sys, "stderr", stderr), mock.patch.object(
                    installer, "describe_or_write"
                ) as write:
                    self.assertEqual(1, installer.main())
                self.assertIn("not executable", stderr.getvalue())
                write.assert_not_called()
                after = {
                    path.relative_to(root): (path.read_bytes(), path.stat().st_mode)
                    for path in root.rglob("*")
                    if path.is_file() and ".git" not in path.relative_to(root).parts
                }
                self.assertEqual(before, after)

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
    def test_source_helper_symlinks_are_not_imported_before_preflight(self) -> None:
        for helper in ("ensure_parent_tracking.py", "verify_install.py", "registry/paths.py"):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory(
                prefix="source-helper-test."
            ) as temporary:
                root = Path(temporary)
                source = root / "project-agent-workflow"
                assemble(source)
                outside = root / "outside.py"
                outside.write_text("print('unexpected helper execution')\n", encoding="utf-8")
                script = source / "scripts" / helper
                script.unlink()
                script.symlink_to(outside)
                target = root / "target"
                subprocess.run(["git", "init", "-q", str(target)], check=True)
                for arguments in (("--list-hosts",), ("--host", "all", "--dry-run"), ("--host", "all")):
                    with self.subTest(arguments=arguments):
                        result = subprocess.run(
                            [sys.executable, "-B", str(source / "scripts/install.py"), "--project", str(target), *arguments],
                            capture_output=True,
                            text=True,
                        )
                        self.assertNotIn("unexpected helper execution", result.stdout + result.stderr)
                        self.assertEqual(0 if arguments == ("--list-hosts",) else 1, result.returncode)
                        if arguments != ("--list-hosts",):
                            self.assertIn("symlink", result.stderr)
                        self.assertFalse((target / ".gitignore").exists())
                        self.assertFalse((target / ".agents").exists())
                        self.assertFalse((target / ".claude").exists())

    def test_tracking_change_after_preflight_stops_all_later_host_writes(self) -> None:
        canonical = ("\n".join(tracking.managed_block_lines(HOSTS)) + "\n").encode()
        for original in (None, b"", b"original/\n", canonical):
            with self.subTest(original=original), tempfile.TemporaryDirectory(
                prefix="tracking-preflight-test."
            ) as temporary:
                root = Path(temporary).resolve()
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                ignore = root / ".gitignore"
                if original is not None:
                    ignore.write_bytes(original)
                changed = b"concurrent user rules/\n"
                original_write = installer.write_preferences_atomic

                def mutate_after_preflight(*args, **kwargs) -> None:
                    original_write(*args, **kwargs)
                    ignore.write_bytes(changed)

                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(
                    sys, "argv", ["install.py", "--project", str(root), "--host", "all", "--language", "vi"]
                ), mock.patch.object(sys, "stdout", stdout), mock.patch.object(
                    sys, "stderr", stderr
                ), mock.patch.object(
                    installer, "write_preferences_atomic", side_effect=mutate_after_preflight
                ), mock.patch.object(installer, "describe_or_write") as write:
                    self.assertEqual(1, installer.main())
                self.assertIn(".gitignore changed", stderr.getvalue())
                write.assert_not_called()
                self.assertEqual(changed, ignore.read_bytes())
                self.assertEqual([], list(root.glob(".project-agent-workflow.gitignore.*")))
                self.assertFalse((root / ".agents/README.md").exists())
                for host in HOSTS:
                    self.assertFalse((root / host.destination).exists())

    def test_tracking_change_during_staging_stops_all_later_host_writes(self) -> None:
        for original in (None, b"original/\n"):
            with self.subTest(original=original), tempfile.TemporaryDirectory(
                prefix="tracking-staging-test."
            ) as temporary:
                root = Path(temporary).resolve()
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                ignore = root / ".gitignore"
                if original is not None:
                    ignore.write_bytes(original)
                changed = b"concurrent user rules/\n"
                original_chmod = tracking.os.chmod
                mutations = []

                def mutate_after_staging(path: Path, mode: int) -> None:
                    original_chmod(path, mode)
                    if Path(path).name.startswith(".project-agent-workflow.gitignore."):
                        mutations.append(path)
                        ignore.write_bytes(changed)

                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(
                    sys, "argv", ["install.py", "--project", str(root), "--host", "all", "--language", "vi"]
                ), mock.patch.object(sys, "stdout", stdout), mock.patch.object(
                    sys, "stderr", stderr
                ), mock.patch.object(
                    tracking.os, "chmod", side_effect=mutate_after_staging
                ), mock.patch.object(installer, "describe_or_write") as write:
                    self.assertEqual(1, installer.main())
                self.assertEqual(1, len(mutations))
                self.assertIn(".gitignore changed", stderr.getvalue())
                write.assert_not_called()
                self.assertEqual(changed, ignore.read_bytes())
                self.assertEqual([], list(root.glob(".project-agent-workflow.gitignore.*")))
                self.assertFalse((root / ".agents/README.md").exists())
                for host in HOSTS:
                    self.assertFalse((root / host.destination).exists())

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
