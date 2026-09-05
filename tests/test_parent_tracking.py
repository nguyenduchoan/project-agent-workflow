#!/usr/bin/env python3
"""Tracking races and recognition of existing host packages."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assemble_skill import assemble


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import ensure_parent_tracking as tracking  # noqa: E402
from registry.hosts import HOSTS  # noqa: E402


class ParentTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="parent-tracking-test.")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.path = self.root / ".gitignore"

    def run_tracking(self, *arguments: str) -> tuple[int, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(
            sys, "argv", ["ensure_parent_tracking.py", "--project", str(self.root), *arguments]
        ), mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
            result = tracking.main()
        return result, stdout.getvalue() + stderr.getvalue()

    def test_update_rechecks_bytes_and_existence_immediately_before_replace(self) -> None:
        cases = (
            (b"original/\n", b"concurrent/\n"),
            (b"", b"concurrent/\n"),
            (None, b"appeared/\n"),
            (None, b""),
            (b"original/\n", b"original/\r\n"),
            (b"original/\n", None),
        )
        original_chmod = tracking.os.chmod
        for original, concurrent in cases:
            with self.subTest(original=original, concurrent=concurrent):
                self.path.unlink(missing_ok=True)
                if original is not None:
                    self.path.write_bytes(original)
                mutations = []

                def mutate_after_staging(path: Path, mode: int) -> None:
                    original_chmod(path, mode)
                    if Path(path).name.startswith(".project-agent-workflow.gitignore."):
                        mutations.append(path)
                        if concurrent is None:
                            self.path.unlink()
                        else:
                            self.path.write_bytes(concurrent)

                with mock.patch.object(
                    tracking.os, "chmod", side_effect=mutate_after_staging
                ), mock.patch.object(tracking.os, "replace", wraps=tracking.os.replace) as replace:
                    result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")

                self.assertEqual(1, len(mutations))
                self.assertEqual(1, result, output)
                self.assertIn(".gitignore changed", output)
                replace.assert_not_called()
                self.assertEqual(concurrent, self.path.read_bytes() if self.path.exists() else None)
                self.assertEqual([], list(self.root.glob(".project-agent-workflow.gitignore.*")))

    def test_concurrent_symlink_is_rejected_without_touching_its_target(self) -> None:
        outside = self.root / "user-ignore"
        outside.write_bytes(b"user rules\n")
        original_chmod = tracking.os.chmod

        def introduce_symlink(path: Path, mode: int) -> None:
            original_chmod(path, mode)
            self.path.symlink_to(outside)

        with mock.patch.object(tracking.os, "chmod", side_effect=introduce_symlink):
            result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(1, result, output)
        self.assertIn("symlink", output)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(b"user rules\n", outside.read_bytes())
        self.assertEqual([], list(self.root.glob(".project-agent-workflow.gitignore.*")))

    def test_missing_file_creation_is_exclusive_even_after_the_final_recheck(self) -> None:
        original_link = tracking.os.link
        concurrent = b"appeared after recheck/\n"

        def create_before_link(source: Path, destination: Path) -> None:
            self.path.write_bytes(concurrent)
            original_link(source, destination)

        with mock.patch.object(tracking.os, "link", side_effect=create_before_link) as link:
            result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        link.assert_called_once()
        self.assertEqual(1, result, output)
        self.assertEqual(concurrent, self.path.read_bytes())
        self.assertEqual([], list(self.root.glob(".project-agent-workflow.gitignore.*")))

    def test_update_preserves_user_bytes_and_mode_and_is_idempotent(self) -> None:
        original = b"# user rules\r\ncache/\r\n"
        self.path.write_bytes(original)
        self.path.chmod(0o600)
        result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(0, result, output)
        expected = original + ("\r\n".join(tracking.managed_block_lines((HOSTS[0],))) + "\r\n").encode()
        self.assertEqual(expected, self.path.read_bytes())
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)
        before = self.path.stat().st_mtime_ns
        result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(0, result, output)
        self.assertIn("unchanged", output)
        self.assertEqual(expected, self.path.read_bytes())
        self.assertEqual(before, self.path.stat().st_mtime_ns)

    def test_selected_host_is_relevant_without_an_installation(self) -> None:
        for host in HOSTS:
            with self.subTest(host=host.id):
                self.assertEqual((host,), tracking.relevant_hosts(self.root, [host.id]))

    def test_arbitrary_destination_is_not_a_recognized_installation(self) -> None:
        for host in HOSTS:
            with self.subTest(host=host.id):
                destination = self.root / host.destination
                destination.mkdir(parents=True)
                self.assertNotIn(host, tracking.relevant_hosts(self.root, []))
                (destination / "unrelated.txt").write_text("user data\n", encoding="utf-8")
                self.assertNotIn(host, tracking.relevant_hosts(self.root, []))

    def test_only_selected_host_rules_are_added_with_an_arbitrary_unselected_directory(self) -> None:
        unselected = HOSTS[-1]
        destination = self.root / unselected.destination
        destination.mkdir(parents=True)
        user_file = destination / "unrelated.txt"
        user_file.write_bytes(b"user data\n")
        result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(0, result, output)
        self.assertEqual(
            "\n".join(tracking.managed_block_lines((HOSTS[0],))) + "\n",
            self.path.read_text(encoding="utf-8"),
        )
        self.assertEqual(b"user data\n", user_file.read_bytes())

    def test_complete_manifest_package_is_recognized_without_running_host_code(self) -> None:
        for host in HOSTS:
            destination = self.root / host.destination
            assemble(destination)
            (destination / "scripts/verify_install.py").write_text(
                "raise RuntimeError('installed code must not run during recognition')\n",
                encoding="utf-8",
            )
        with mock.patch.object(tracking.subprocess, "run") as run:
            self.assertEqual(HOSTS, tracking.relevant_hosts(self.root, []))
        run.assert_not_called()

    def test_standalone_helper_never_imports_a_symlinked_source_verifier(self) -> None:
        source = self.root / "source/project-agent-workflow"
        assemble(source)
        verifier = source / "scripts/verify_install.py"
        verifier.unlink()
        outside = self.root / "outside.py"
        outside.write_text(
            "print('unexpected verifier execution')\n"
            "EXPECTED_SKILL_FILES = ()\n"
            "def verify_runtime_manifest(skill_root, errors): pass\n",
            encoding="utf-8",
        )
        verifier.symlink_to(outside)
        for installed in (False, True):
            if installed:
                assemble(self.root / HOSTS[-1].destination)
            for mode in ("--check", "--dry-run", "--apply"):
                with self.subTest(installed=installed, mode=mode):
                    result = subprocess.run(
                        [sys.executable, "-B", str(source / "scripts/ensure_parent_tracking.py"),
                         "--project", str(self.root), "--host", HOSTS[0].id, mode],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotIn("unexpected verifier execution", result.stdout + result.stderr)
                    self.assertEqual(1 if installed else 0, result.returncode, result.stderr)
                    if installed:
                        self.assertIn("symlink", result.stderr)

    def test_standalone_helper_does_not_import_unneeded_source_modules(self) -> None:
        source = self.root / "source/project-agent-workflow"
        assemble(source)
        paths = source / "scripts/registry/paths.py"
        paths.unlink()
        outside = self.root / "outside.py"
        outside.write_text(
            "print('unexpected module execution')\n"
            "def has_symlink_component(root, path): return False\n",
            encoding="utf-8",
        )
        paths.symlink_to(outside)
        assemble(self.root / HOSTS[-1].destination)
        for mode in ("--check", "--dry-run", "--apply"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-B", str(source / "scripts/ensure_parent_tracking.py"),
                     "--project", str(self.root), "--host", HOSTS[0].id, mode],
                    capture_output=True,
                    text=True,
                )
                self.assertNotIn("unexpected module execution", result.stdout + result.stderr)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_incomplete_foreign_or_invalid_manifest_package_is_not_recognized(self) -> None:
        host = HOSTS[-1]
        cases = ("missing-file", "foreign-name", "duplicate", "traversal", "non-manifest")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / host.destination
                assemble(destination)
                if case == "missing-file":
                    (destination / "VERSION").unlink()
                elif case == "foreign-name":
                    (destination / "SKILL.md").write_text(
                        "---\nname: unrelated-skill\ndescription: Another skill\n---\n", encoding="utf-8"
                    )
                elif case == "non-manifest":
                    (destination / "unexpected.txt").write_text("user data\n", encoding="utf-8")
                else:
                    with (destination / "skill-manifest.txt").open("a", encoding="utf-8") as stream:
                        stream.write("VERSION\n" if case == "duplicate" else "../outside\n")
                self.assertNotIn(host, tracking.relevant_hosts(root, []))

    def test_symlink_at_any_host_path_component_is_not_followed_for_recognition(self) -> None:
        host = HOSTS[-1]
        components = Path(host.destination).parts
        for index in range(len(components) + 1):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / host.destination
                if index < len(components):
                    link = root.joinpath(*components[:index + 1])
                    target = root / "outside"
                    target.mkdir()
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target, target_is_directory=True)
                else:
                    assemble(destination)
                    link = destination / "scripts/registry/paths.py"
                    link.unlink()
                    target = root / "outside.py"
                    target.write_text("user code\n", encoding="utf-8")
                    link.symlink_to(target)
                with mock.patch.object(Path, "read_text", side_effect=AssertionError("symlink read")):
                    self.assertNotIn(host, tracking.relevant_hosts(root, []))

    def test_existing_unselected_tracking_is_preserved_without_a_valid_installation(self) -> None:
        original = "\n".join(tracking.managed_block_lines(HOSTS)) + "\n"
        self.path.write_text(original, encoding="utf-8")
        result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(0, result, output)
        self.assertIn("unchanged", output)
        self.assertEqual(original, self.path.read_text(encoding="utf-8"))

    def test_unknown_rules_in_managed_block_still_fail_closed(self) -> None:
        content = "\n".join(tracking.managed_block_lines(HOSTS)).replace(
            tracking.END, "!/unregistered/**\n" + tracking.END
        ) + "\n"
        self.path.write_text(content, encoding="utf-8")
        result, output = self.run_tracking("--host", HOSTS[0].id, "--apply")
        self.assertEqual(1, result, output)
        self.assertIn("was edited", output)
        self.assertEqual(content, self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
