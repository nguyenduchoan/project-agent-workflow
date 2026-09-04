# Installation

## Supported target

The default target is the root of the Git repository containing the current
working directory. Requirements are Git, a POSIX-compatible shell, and Python
3.10 or newer for validation.

Run one command from a new project, using the absolute path of a trusted checkout
of this skill:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh
```

To target another repository explicitly:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh --project /path/to/repository
```

Inspect planned changes without writing:

```sh
bash /absolute/path/to/project-agent-workflow/install.sh --dry-run
```

## Safety contract

- The target must resolve to a Git repository root.
- Workflow files stay below `<repo>/.agents`. The only additional write is a
  marked allow block in the repository-root `.gitignore`:

  ```gitignore
  # project-agent-workflow: begin
  !/.agents/
  !/.agents/**
  # project-agent-workflow: end
  ```
- A symlink in a managed destination path stops installation.
- A missing file is created atomically; an identical file is left unchanged.
- A different existing file is a conflict and stops the entire preflight before
  any file is written.
- Existing `.gitignore` content is preserved. Missing or canonical managed blocks
  are appended/moved to the end; malformed or duplicated markers fail closed.
- The installer never edits `AGENTS.md`, `.git/hooks`, user-level Codex
  directories, or global Git configuration, and never stages or commits files.
- The installer does not copy project-specific task history, architecture state,
  legacy exceptions, credentials, or runtime evidence.

The conflict policy is intentionally conservative. Review and reconcile a
different existing file manually, then run the installer again. Do not bypass
ownership by deleting or overwriting project state automatically.

## Result

The installation adds the skill at
`.agents/skills/project-agent-workflow` and initializes generic task,
architecture, policy, and review-report templates under `.agents`. Codex discovers
the skill from the repository-scoped `.agents/skills` location. The full `.agents`
tree is intended to be reviewed, staged, committed, and synchronized by the parent
repository.

Run the post-install check:

```sh
python3 .agents/skills/project-agent-workflow/scripts/verify_install.py --project .
```

Restart Codex only if the newly installed skill does not appear automatically.

After reviewing the generated files, stage them in the parent repository:

```sh
git add .gitignore .agents
```
