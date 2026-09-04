# Contributing

Contributions should keep the package generic, dependency-light, and safe to run
inside an existing Git repository.

## Development workflow

1. Create a focused branch and explain the user-facing behavior being changed.
2. Keep product names, internal URLs, credentials, runtime payloads, and private
   project history out of every source and fixture.
3. Add every installed runtime file to `skill-manifest.txt`; keep the manifest
   sorted. Release-only documentation and tests must stay outside the manifest.
4. Preserve the installer's fail-closed behavior for conflicts, symlinks, malformed
   tracking markers, and non-Git targets.
5. Run the full local gate:

   ```sh
   python3 tests/verify_package.py
   sh -n install.sh scripts/install.sh tests/test_install.sh
   python3 -m compileall -q scripts tests
   python3 -m unittest discover -s tests -p 'test_*.py'
   ./tests/test_install.sh
   ```

## Compatibility

Changes must remain compatible with Python 3.10 or newer and a POSIX-compatible
shell unless a proposal explicitly changes the supported platform matrix. Avoid
third-party runtime dependencies for installer and validator paths.

## Pull requests

Include the validation commands and results, compatibility impact, security
considerations, and rollback behavior. A change that adds an overwrite flag,
network execution, hook installation, user-level configuration, or writes outside
the documented target boundary requires explicit design review.

## Releasing

`VERSION` identifies source and installed package contents; it does not prove a Git
tag or GitHub release exists. A release owner creates those objects only after the
reviewed source passes this checklist:

- [ ] package verification passes;
- [ ] installer and validator tests pass;
- [ ] the manifest-assembled artifact passes official Agent Skills validation;
- [ ] `VERSION`, the changelog heading, intended `v<version>` tag, and installed
      artifact version agree;
- [ ] the changelog entry is no longer marked `Unreleased`;
- [ ] Git status contains only reviewed release changes before tagging;
- [ ] manual security review confirms installer boundaries, prompt-injection
      guidance, and secret guards remain intact.

After the checklist passes, merge through the normal review process, create a
signed `v<version>` tag from that reviewed commit, and publish source archives from
that tag. No repository script creates or pushes a tag or release automatically.
