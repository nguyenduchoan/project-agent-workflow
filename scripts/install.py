#!/usr/bin/env python3
"""Generic, transaction-like project installer for trusted Agent Skills hosts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

from registry.findings import ConfigurationError
from registry.hosts import HOSTS, HostDefinition, expand_host_ids, validate_registry
from registry.preferences import load_preferences, serialize, validate_language_tag, with_language


def die(message: str) -> int:
    print(f"project-agent-workflow-installer: ERROR: {message}", file=sys.stderr)
    return 1


def git_root(project: Path) -> Path:
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"target is not inside a Git repository: {project}")
    return Path(result.stdout.strip()).resolve()


def parse_manifest(path: Path) -> list[str]:
    entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(entries) != len(set(entries)):
        raise ValueError("duplicate runtime manifest entry: " + next(item for item in entries if entries.count(item) > 1))
    for entry in entries:
        p = Path(entry)
        if p.is_absolute() or entry.startswith("./") or ".." in p.parts:
            raise ValueError(f"unsafe runtime manifest entry: {entry}")
    return entries


def assert_no_symlink(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f"managed destination escapes repository: {path}")
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError(f"refusing symlink in managed path: {current}")


def assert_destination(host: HostDefinition, repo: Path) -> Path:
    destination = PurePosixPath(host.destination)
    owned_root = PurePosixPath(host.owned_root)
    try:
        destination.relative_to(owned_root)
    except ValueError:
        raise ValueError(f"unsafe host destination: {host.destination}")
    target = repo.joinpath(*destination.parts)
    assert_no_symlink(target, repo)
    return target


def source_files(source: Path, entries: list[str]) -> list[tuple[Path, str]]:
    result = []
    for entry in entries:
        src = source / entry
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"runtime manifest entry is missing or is a symlink: {entry}")
        if entry.startswith("assets/project-template/"):
            result.append((src, entry.removeprefix("assets/project-template/")))
    return result


def runtime_files(source: Path, entries: list[str]) -> list[tuple[Path, str]]:
    result = []
    for entry in entries:
        src = source / entry
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"runtime manifest entry is missing or is a symlink: {entry}")
        result.append((src, entry))
    return result


def check_file(
    src: Path,
    dst: Path,
    repo: Path,
    *,
    changed_during_install: bool = False,
) -> None:
    assert_no_symlink(dst, repo)
    if dst.exists():
        if not dst.is_file() or not os.path.isfile(dst):
            raise ValueError(f"managed destination is not a regular file: {dst}")
        if not os.path.samefile(src, dst) and not _same_content(src, dst):
            if changed_during_install:
                raise ValueError(f"managed destination changed during install: {dst}")
            raise ValueError(f"conflict: existing file differs and will not be overwritten: {dst}")
        if src.suffix.lower() in {".py", ".sh"} and dst.stat().st_mode & 0o022:
            raise ValueError(f"managed runtime code is group/world-writable: {dst}")


def _same_content(left: Path, right: Path) -> bool:
    return left.read_bytes() == right.read_bytes()


def check_tree(root: Path, manifest: set[str], repo: Path) -> None:
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"managed root is not a directory: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"refusing symlink in managed path: {path}")
        if path.is_file() and path.relative_to(root).as_posix() not in manifest:
            raise ValueError(f"conflict: installed skill contains a non-manifest path: {path}")


def describe_or_write(src: Path, dst: Path, repo: Path, dry_run: bool) -> None:
    if dst.exists():
        check_file(src, dst, repo, changed_during_install=True)
        print(f"project-agent-workflow-installer: unchanged {dst.relative_to(repo)}")
        return
    if dry_run:
        print(f"project-agent-workflow-installer: would create {dst.relative_to(repo)}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    assert_no_symlink(dst, repo)
    fd, temporary = tempfile.mkstemp(prefix=".project-agent-workflow.", dir=str(dst.parent))
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copyfile(src, temporary_path)
        os.chmod(temporary_path, 0o755 if src.stat().st_mode & 0o111 else 0o644)
        os.link(temporary_path, dst)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"project-agent-workflow-installer: created {dst.relative_to(repo)}")


def write_preferences_atomic(
    path: Path,
    content: str,
    repo: Path,
    expected_content: bytes | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_no_symlink(path, repo)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".project-agent-workflow.preferences.", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o644)
        if path.is_symlink():
            raise ValueError(f"preferences path became a symlink: {path}")
        current_content = path.read_bytes() if path.exists() else None
        if current_content != expected_content:
            raise ValueError(f"preferences changed during installation: {path}")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--language")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-hosts", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("path", nargs="?")
    return parser.parse_args()


def usage() -> None:
    print("""Usage: install.sh [options] [project]

Options:
  --host <id>         Install for a host; repeatable; selector: all
  --list-hosts        List built-in hosts and exit
  --language <tag>    Preferred language for responses and generated documents.
                      Examples: vi, en, ja, th, pt-BR
  --project <path>    Target Git repository
  --dry-run           Show the complete plan without writing
  -h, --help          Show this help

With no --host, an interactive TTY prompts for one or more hosts and language.
Non-interactive automation should pass --host explicitly.""")


def interactive_hosts() -> list[str]:
    print("Project Agent Workflow Installer\n\nSelect one or more hosts:\n")
    for index, host in enumerate(HOSTS, 1):
        print(f"  [{index}] {host.display_name}")
    print("\nCommands:\n  a = all\n  q = quit\n\nSelection:")
    value = input("> ").strip()
    if value.lower() == "q":
        raise ValueError("installation cancelled")
    tokens = value.replace(",", " ").split()
    if "a" in [token.lower() for token in tokens]:
        return ["all"]
    ids = []
    for token in tokens:
        if not token.isdigit() or not 1 <= int(token) <= len(HOSTS):
            raise ValueError(f"unknown host selection '{token}'")
        ids.append(HOSTS[int(token) - 1].id)
    return ids


def interactive_language() -> str:
    print("\nLanguage preference (shared across installed hosts):\n\n  [1] Vietnamese (vi)\n  [2] English (en)\n  [3] Default / English (en)\n  [4] Other language tag\n\nSelection:")
    choice = input("> ").strip()
    if choice == "1":
        return "vi"
    if choice == "2":
        return "en"
    if choice == "3":
        return "en"
    if choice == "4":
        return validate_language_tag(input("Enter language tag: ").strip())
    raise ValueError("unknown language selection")


def main() -> int:
    args = parse_args()
    if args.help:
        usage()
        return 0
    if args.list_hosts:
        try:
            validate_registry()
        except (ConfigurationError, ValueError) as exc:
            return die(str(exc))
        print("Available hosts:\n")
        for host in HOSTS:
            print(f"  {host.id:<13} {host.display_name}")
        return 0
    if args.project and args.path:
        return die("--project and positional project path cannot both be used")
    project = (args.project or Path(args.path) if args.project or args.path else Path.cwd()).resolve()
    try:
        host_values = list(args.host)
        language = args.language
        if not host_values:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                choices = "\n".join(f"  --host {host.id}" for host in HOSTS)
                return die(
                    "no interactive terminal detected.\n\nSpecify one or more hosts:\n\n"
                    f"{choices}\n  --host all"
                )
            host_values = interactive_hosts()
            if language is None:
                language = interactive_language()
        hosts = expand_host_ids(host_values)
        if not hosts:
            raise ValueError("at least one host must be selected")
        if language is not None:
            language = validate_language_tag(language)
        repo = git_root(project)
        source = Path(__file__).resolve().parent.parent
        manifest = parse_manifest(source / "skill-manifest.txt")
        template_files = source_files(source, manifest)
        runtime = runtime_files(source, manifest)
        for host in hosts:
            destination = assert_destination(host, repo)
            check_tree(destination, {name for _, name in runtime}, repo)
            for src, relative in runtime:
                check_file(src, destination / relative, repo)
        shared_root = repo
        assert_no_symlink(shared_root, repo)
        if (repo / ".agents/.git").exists():
            raise ValueError("nested .agents Git mode is project-specific and is not supported by this installer")
        for src, relative in template_files:
            check_file(src, shared_root / relative, repo)
        preferences_path = repo / ".agents" / "preferences.json"
        preferences_existed = preferences_path.exists()
        existing_preferences = load_preferences(preferences_path)
        expected_preferences_content = (
            preferences_path.read_bytes() if preferences_existed else None
        )
        preferences = existing_preferences
        if language is None and not preferences_path.exists():
            language = "en"
        if language is not None:
            preferences = with_language(existing_preferences, language)
            print(f"project-agent-workflow-installer: Language preference: {language}")
        tracking = source / "scripts/ensure_parent_tracking.py"
        tracking_command = [sys.executable, str(tracking), "--project", str(repo)]
        for host in hosts:
            tracking_command.extend(["--host", host.id])
        subprocess.run([*tracking_command, "--check"], check=True)
        if language is not None or preferences_path.exists():
            if preferences_path.exists() and not preferences_path.is_file():
                raise ValueError("preferences path must be a regular file")
            if preferences != existing_preferences:
                if not args.dry_run:
                    write_preferences_atomic(
                        preferences_path,
                        serialize(preferences),
                        repo,
                        expected_preferences_content,
                    )
                action = (
                    "would update" if args.dry_run and preferences_existed
                    else "would create" if args.dry_run
                    else "updated" if preferences_existed
                    else "created"
                )
                print(f"project-agent-workflow-installer: {action} .agents/preferences.json")
            elif language is not None:
                print("project-agent-workflow-installer: unchanged .agents/preferences.json")
        if not args.dry_run:
            subprocess.run([*tracking_command, "--apply"], check=True)
        else:
            print("project-agent-workflow-installer: dry-run shared tracking check")
        for src, relative in template_files:
            describe_or_write(src, shared_root / relative, repo, args.dry_run)
        for host in hosts:
            destination = repo / host.destination
            for src, relative in runtime:
                describe_or_write(src, destination / relative, repo, args.dry_run)
        if args.dry_run:
            print("project-agent-workflow-installer: dry-run complete; no files written")
        else:
            verifier = repo / hosts[0].destination / "scripts/verify_install.py"
            verification_command = [sys.executable, str(verifier), "--project", str(repo)]
            for host in hosts:
                verification_command.extend(["--host", host.id])
            subprocess.run(verification_command, check=True)
            print(f"project-agent-workflow-installer: installation complete for {repo}")
        return 0
    except (ConfigurationError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        return die(str(exc))


if __name__ == "__main__":
    sys.exit(main())
