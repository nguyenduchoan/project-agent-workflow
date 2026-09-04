# Security Policy

## Supported versions

Security fixes are applied to the latest released minor version. Maintainers may
issue a patch for an older line when a safe upgrade is not practical.

## Reporting a vulnerability

Use the hosting platform's private vulnerability-reporting channel when available,
or contact the maintainers through a private channel listed by the published
repository. Do not include credentials, private keys, production payloads, or
personal data in a public issue.

Include the affected version, target platform, minimal reproduction, expected
boundary, observed behavior, and whether the issue can overwrite files, follow a
symlink, escape the target repository, expose sensitive data, or execute untrusted
content.

## Threat model

The installer treats both its source checkout and target repository as potentially
surprising filesystem input. It therefore:

- resolves the target through Git and anchors managed paths below its root;
- refuses symlinks in managed source or destination paths;
- completes conflict checks before writing;
- creates missing files without overwriting an existing path;
- changes only `.agents` and one marked block in the root `.gitignore`;
- never installs hooks, modifies user-level configuration, stages, commits, or
  executes commands stored in registry records.

Users must still inspect and trust the release they execute. Prefer a pinned tag or
commit and avoid piping an unreviewed network response directly into a shell.
