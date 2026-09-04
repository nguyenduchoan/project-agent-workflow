# Project Agent Workflow

A repository-scoped Agent Skill for durable task records, progressive architecture
context, review evidence, and lightweight architecture-change gates.

The installer puts both the skill and its generic workflow under the target
repository's `.agents` directory. The parent Git repository tracks the complete
tree, so a normal clone, branch, pull request, or merge carries the same workflow
to every contributor.

## Why this exists

Agent instructions often start as useful project notes and then become a large,
unversioned context dump. This package keeps the reusable workflow small and
separates it from product-specific history:

- `SKILL.md` contains routing and core rules.
- `references/` contains procedures loaded only when needed.
- `assets/project-template/` contains an empty, generic `.agents` registry.
- `scripts/` installs and validates without third-party Python packages.
- `skill-manifest.txt` locks the exact files allowed into a target project.

It follows the Agent Skills directory shape and includes Codex UI metadata in
`agents/openai.yaml`. See the [Agent Skills specification](https://agentskills.io/specification)
and [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills).

## Requirements

- Git
- A POSIX-compatible shell
- Python 3.10 or newer

The test suite covers macOS and Linux-style environments. Native PowerShell is not
currently supported; on Windows, use WSL or another POSIX-compatible environment.

## Install into a project

Review or clone a trusted release of this repository, enter the target Git
repository, then run one command:

```sh
/absolute/path/to/project-agent-workflow/install.sh
```

An explicit target is also supported:

```sh
/absolute/path/to/project-agent-workflow/install.sh --project /path/to/repository
```

Preview the result without writing:

```sh
/absolute/path/to/project-agent-workflow/install.sh --dry-run
```

Consumers can keep one trusted local checkout and use the installation command
above for every new project. After `v1.0.0` is tagged, they can also clone that
pinned release to a temporary path and install in one shell command:

```sh
git clone --depth 1 --branch v1.0.0 https://github.com/nguyenduchoan/skill-agile-agent.git /tmp/project-agent-workflow-v1.0.0 && /tmp/project-agent-workflow-v1.0.0/install.sh --project "$PWD"
```

The installer deliberately does not stage or commit. Review the generated files,
then add them through the target repository's normal process:

```sh
git add .gitignore .agents
```

Prefer committing the generated tree as one focused commit. Roll back a committed
installation through the repository's normal `git revert` review flow. Before
commit, inspect `git status` and remove only paths reported as created by the
installer; there is intentionally no broad automatic uninstaller.

## Installed layout

```text
.agents/
├── skills/project-agent-workflow/
├── tasks/{active,history,templates}/
├── architecture/{branches,changes,templates}/
├── policies/
└── reviews/templates/
```

The root `.gitignore` receives one marked allow block so `.agents`, including its
`scripts` and `assets`, remains visible even when a developer has broad global Git
ignore rules.

## Safety properties

- Target discovery is anchored to the target Git root.
- Writes are limited to `.agents` plus the marked root `.gitignore` block.
- The complete conflict and symlink preflight runs before the first write.
- Existing different files are never overwritten.
- Reinstalling the same version is idempotent.
- No nested Git repository, Git hook, user configuration, staging, or commit is
  created.
- The installed package contains only files listed in `skill-manifest.txt`.
- Validators reject high-confidence credential patterns and project-specific
  content in the distribution.

Read [references/installation.md](references/installation.md) for the complete
installation contract and [SECURITY.md](SECURITY.md) for the threat model.

## Validate and test

```sh
python3 tests/verify_package.py
sh -n install.sh scripts/install.sh tests/test_install.sh
python3 -m compileall -q scripts tests
./tests/test_install.sh
```

The end-to-end suite creates temporary Git repositories and tests fresh install,
dry-run, idempotency, ignore precedence, conflict refusal, symlink boundaries,
nested-Git refusal, architecture gates, broken links, and secret detection.

## Scope and non-goals

This package provides workflow primitives, not product architecture. It does not
ship branch history, application flow snapshots, provider names, legacy policy
exceptions, production evidence, or organization-specific hooks. Projects remain
free to extend the generated templates through their own `AGENTS.md` and review
policy. Automatic in-place upgrades are intentionally outside the v1 installer:
reconcile project-owned changes and version updates through review instead of an
overwrite flag.

## License

MIT. See [LICENSE](LICENSE).
