# Installation

## Supported target

The default target is the root of the Git repository containing the current
working directory. Requirements are Git, a POSIX-compatible shell, and Python
3.10 or newer for validation.

Run one command from a new project, using the absolute path of a trusted checkout
of this skill. In an interactive TTY, the installer asks for hosts and a shared
language preference. Automation must select hosts explicitly:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh --host codex --language en
```

To target another repository explicitly:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh \
  --host codex --project /path/to/repository
```

List trusted built-in hosts with `--list-hosts`. The first-class adapters are
Codex (`.agents/skills/project-agent-workflow`) and Claude Code
(`.claude/skills/project-agent-workflow`). Workflow state remains shared and
host-neutral under `.agents`; host packages only provide discovery files.

Repeat `--host` for multiple selections or use `--host all`. `all` expands to the
current trusted registry before preflight and is not itself a host. A no-TTY run
without `--host` fails instead of blocking or silently choosing Codex.

Use any validated BCP-47-style language tag:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh \
  --host all --language pt-BR --project /path/to/repository
```

Interactive installs prompt for language. Fresh non-interactive installs default
deterministically to `en` when `--language` is absent. Existing preferences are
preserved without the flag; an explicit flag updates only the shared language
fields and preserves unrelated JSON keys.

A positional project path is equivalent and is supported by both wrappers:

```sh
bash /absolute/path/to/project-agent-workflow/scripts/install.sh \
  --host codex /path/to/repository
```

Inspect planned changes without writing:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh --host all --dry-run
```

## Safety contract

- The target must resolve to a Git repository root.
- Shared workflow files stay below `<repo>/.agents`; selected host discovery
  packages use only trusted registered destinations. The only additional write is a
  marked allow block in the repository-root `.gitignore`. The block always tracks
  `.agents` and adds narrow host-specific rules only for selected hosts plus
  recognized already-installed hosts. A fresh Codex-only install therefore has the
  minimal block:

  ```gitignore
  # project-agent-workflow: begin
  !/.agents/
  !/.agents/**
  # project-agent-workflow: end
  ```

  Selecting or recognizing an installed Claude Code package adds its existing
  narrow `.claude/skills/project-agent-workflow` rules without exposing unrelated
  `.claude` files.
- A symlink in a managed destination path stops installation.
- Package runtime `.py` and `.sh` files must not be group- or world-writable.
- A missing file is created atomically; an identical existing file is content- and
  path-rechecked immediately before it is reported unchanged.
- A different existing file is a conflict and stops the entire preflight before
  any file is written. If content changes after preflight, the write phase fails
  closed without overwriting it or writing later hosts.
- Every selected host and the shared preference change pass one complete preflight
  before mutation, preventing a later-host conflict from causing partial install.
- Existing `.gitignore` content is preserved. Missing or canonical managed blocks
  are appended/moved to the end; malformed or duplicated markers fail closed.
- The installer never accepts an arbitrary destination, executes host/preference
  data, edits `AGENTS.md`, `.git/hooks`, `.claude/settings.json`, `.claude/hooks`,
  `.claude/commands`, user-level host directories, or global Git configuration,
  and never stages or commits files.
- It never pushes, uses `sudo`, or requires runtime network access.
- The installer does not copy project-specific task history, architecture state,
  legacy exceptions, credentials, or runtime evidence.

The conflict policy is intentionally conservative. Review and reconcile a
different existing file manually, then run the installer again. Do not bypass
ownership by deleting or overwriting project state automatically.

## Result

The installation initializes generic task, architecture, policy, and review-report
templates under `.agents`, plus the selected host discovery packages. Shared
language defaults are stored in `.agents/preferences.json`; structured metadata
keys and enum values remain canonical and language-independent.

The installer runs post-write verification for shared state and only the hosts in
that installation operation. Run an explicit full audit of every recognized
installed host with:

```sh
python3 .agents/skills/project-agent-workflow/scripts/verify_install.py \
  --project . --all-installed-hosts
```

Use repeatable `--host <id>` instead when manually verifying a specific operation.
Normal registry validation also validates `.agents/preferences.json` when present.

Restart the selected host only if the newly installed skill does not appear
automatically.

After reviewing the generated files, stage them in the parent repository:

```sh
git add .gitignore .agents .claude/skills/project-agent-workflow
```

Omit the `.claude` path when Claude Code was not selected.
