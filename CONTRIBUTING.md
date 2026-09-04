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

1. Update `VERSION` and `CHANGELOG.md` together.
2. Run the full local gate and review the complete Git diff.
3. Merge only after the pinned CI workflow passes on the minimum Python version.
4. Create a signed `v<version>` tag from the reviewed commit and publish source
   archives from that tag.
5. For the first public release, verify the canonical repository URL and version
   used by the one-command example in `README.md`.
