#!/bin/sh

set -eu

program="project-agent-workflow-tests"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
package_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
installer="$package_root/install.sh"
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/project-agent-workflow-test.XXXXXX")
tests_run=0
global_ignore="$temporary_root/global-ignore"
printf '%s\n' ".agents" "scripts" "assets" >"$global_ignore"

cleanup() {
  base_name=$(basename -- "$temporary_root")
  case "$base_name" in
    project-agent-workflow-test.*)
      [ -d "$temporary_root" ] && rm -rf -- "$temporary_root"
      ;;
    *)
      printf '%s: refusing unsafe cleanup path: %s\n' "$program" "$temporary_root" >&2
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf '%s: FAIL: %s\n' "$program" "$*" >&2
  exit 1
}

pass() {
  tests_run=$((tests_run + 1))
  printf '%s: ok %s - %s\n' "$program" "$tests_run" "$1"
}

init_repo() {
  target=$1
  mkdir -p "$target"
  git -C "$target" init -q
  git -C "$target" branch -M main
  git -C "$target" config user.name "Installer Test"
  git -C "$target" config user.email "installer-test@example.invalid"
  git -C "$target" config core.excludesFile "$global_ignore"
  printf '%s\n' "# Test repository" >"$target/README.md"
  git -C "$target" add README.md
  git -C "$target" commit -qm "initial"
}

snapshot() (
  cd "$1"
  cksum .gitignore
  find .agents -type f -exec cksum {} \; | LC_ALL=C sort
)

fresh="$temporary_root/fresh"
init_repo "$fresh"
(cd "$fresh" && "$installer") >"$temporary_root/fresh.out"
validator="$fresh/.agents/skills/project-agent-workflow/scripts/validate_registry.py"
[ -f "$fresh/.agents/skills/project-agent-workflow/SKILL.md" ] || fail "skill was not installed"
[ -f "$fresh/.agents/tasks/templates/task.md" ] || fail "task template was not installed"
[ -f "$fresh/.agents/skills/project-agent-workflow/LICENSE" ] || fail "skill license was not installed"
cmp -s "$package_root/VERSION" "$fresh/.agents/skills/project-agent-workflow/VERSION" ||
  fail "installed VERSION does not match the source package"
[ -x "$fresh/.agents/skills/project-agent-workflow/install.sh" ] ||
  fail "installed one-command wrapper is missing or not executable"
[ ! -e "$fresh/.agents/skills/project-agent-workflow/README.md" ] ||
  fail "release-only README was copied into the installed skill"
[ ! -e "$fresh/.agents/skills/project-agent-workflow/tests" ] ||
  fail "release-only tests were copied into the installed skill"
[ -f "$fresh/.gitignore" ] || fail "parent .gitignore was not created"
grep -Fqx "!/.agents/**" "$fresh/.gitignore" || fail "parent tracking allow rule is missing"
[ ! -e "$fresh/.agents/.git" ] || fail "installer created a nested Git repository"
[ ! -e "$fresh/.git/hooks/post-commit" ] || fail "installer modified parent Git hooks"
if git -C "$fresh" check-ignore --no-index -q -- \
  .agents/skills/project-agent-workflow/scripts/install.sh; then
  fail "parent Git still ignores an installed script"
fi
git -C "$fresh" status --short --untracked-files=all | \
  grep -Fq ".agents/skills/project-agent-workflow/scripts/install.sh" ||
  fail "installed skill is not visible to parent Git"
python3 "$fresh/.agents/skills/project-agent-workflow/scripts/verify_install.py" \
  --project "$fresh" >"$temporary_root/verify.out"
pass "one-command install from the target working directory"

spaced_source="$temporary_root/source package"
spaced_target="$temporary_root/target project"
cp -R "$package_root" "$spaced_source"
init_repo "$spaced_target"
sh "$spaced_source/install.sh" --project "$spaced_target" >"$temporary_root/spaced.out"
[ -f "$spaced_target/.agents/skills/project-agent-workflow/SKILL.md" ] ||
  fail "installer failed when source and target paths contained spaces"
pass "source and target paths with spaces"

duplicate_manifest_target="$temporary_root/duplicate-manifest-target"
init_repo "$duplicate_manifest_target"
printf '%s\n' "SKILL.md" >>"$spaced_source/skill-manifest.txt"
if sh "$spaced_source/install.sh" --project "$duplicate_manifest_target" \
  >"$temporary_root/duplicate-manifest.out" 2>&1; then
  fail "installer accepted a duplicate runtime manifest entry"
fi
grep -Fq "duplicate runtime manifest entry: SKILL.md" \
  "$temporary_root/duplicate-manifest.out" ||
  fail "installer did not report the duplicate runtime manifest entry"
[ ! -e "$duplicate_manifest_target/.agents" ] ||
  fail "duplicate manifest failure caused a partial install"
[ ! -e "$duplicate_manifest_target/.gitignore" ] ||
  fail "duplicate manifest failure changed parent .gitignore"
pass "duplicate runtime manifest entry is rejected before mutation"

traversal_manifest_target="$temporary_root/traversal-manifest-target"
init_repo "$traversal_manifest_target"
cp "$package_root/skill-manifest.txt" "$spaced_source/skill-manifest.txt"
printf '%s\n' "../outside" >>"$spaced_source/skill-manifest.txt"
if sh "$spaced_source/install.sh" --project "$traversal_manifest_target" \
  >"$temporary_root/traversal-manifest.out" 2>&1; then
  fail "installer accepted a traversal runtime manifest entry"
fi
grep -Fq "unsafe runtime manifest entry: ../outside" \
  "$temporary_root/traversal-manifest.out" ||
  fail "installer did not report the traversal runtime manifest entry"
[ ! -e "$traversal_manifest_target/.agents" ] ||
  fail "traversal manifest failure caused a partial install"
[ ! -e "$traversal_manifest_target/.gitignore" ] ||
  fail "traversal manifest failure changed parent .gitignore"
pass "traversal runtime manifest entry is rejected before mutation"

positional_target="$temporary_root/positional-target"
init_repo "$positional_target"
sh "$installer" "$positional_target" >"$temporary_root/positional.out"
[ -f "$positional_target/.agents/skills/project-agent-workflow/SKILL.md" ] ||
  fail "installer did not accept a positional project path"
pass "positional project path"

before=$(snapshot "$fresh")
sh "$installer" --project "$fresh" >"$temporary_root/reinstall.out"
after=$(snapshot "$fresh")
[ "$before" = "$after" ] || fail "idempotent reinstall changed installed content"
pass "idempotent reinstall"

unexpected_runtime="$fresh/.agents/skills/project-agent-workflow/unexpected.md"
printf '%s\n' "unexpected" >"$unexpected_runtime"
if python3 "$fresh/.agents/skills/project-agent-workflow/scripts/verify_install.py" \
  --project "$fresh" >"$temporary_root/unexpected-runtime.out" 2>&1; then
  fail "install verifier accepted a non-manifest runtime file"
fi
grep -Fq "installed skill contains a non-manifest file" \
  "$temporary_root/unexpected-runtime.out" ||
  fail "install verifier did not report a non-manifest runtime file"
rm -f "$unexpected_runtime"
pass "installed runtime is manifest-locked"

unexpected_preflight="$temporary_root/unexpected-preflight"
init_repo "$unexpected_preflight"
mkdir -p "$unexpected_preflight/.agents/skills/project-agent-workflow"
printf '%s\n' "project-owned" \
  >"$unexpected_preflight/.agents/skills/project-agent-workflow/unexpected.md"
if sh "$installer" --project "$unexpected_preflight" \
  >"$temporary_root/unexpected-preflight.out" 2>&1; then
  fail "installer accepted a non-manifest file in its owned skill directory"
fi
[ ! -e "$unexpected_preflight/.gitignore" ] ||
  fail "non-manifest preflight changed parent .gitignore"
[ ! -e "$unexpected_preflight/.agents/skills/project-agent-workflow/SKILL.md" ] ||
  fail "non-manifest preflight caused a partial install"
pass "non-manifest skill conflict is rejected before mutation"

dry_run="$temporary_root/dry-run"
init_repo "$dry_run"
sh "$installer" --dry-run --project "$dry_run" >"$temporary_root/dry-run.out"
[ ! -e "$dry_run/.agents" ] || fail "dry-run created .agents"
[ ! -e "$dry_run/.gitignore" ] || fail "dry-run created .gitignore"
pass "dry-run makes no changes"

conflict="$temporary_root/conflict"
init_repo "$conflict"
mkdir -p "$conflict/.agents"
printf '%s\n' "project-owned content" >"$conflict/.agents/README.md"
if sh "$installer" --project "$conflict" >"$temporary_root/conflict.out" 2>&1; then
  fail "installer accepted a different existing managed file"
fi
[ "$(cat "$conflict/.agents/README.md")" = "project-owned content" ] ||
  fail "conflicting file was changed"
[ ! -e "$conflict/.agents/skills" ] || fail "preflight conflict caused a partial install"
[ ! -e "$conflict/.gitignore" ] || fail "preflight conflict changed parent .gitignore"
pass "conflict refusal is non-mutating"

existing_ignore="$temporary_root/existing-ignore"
init_repo "$existing_ignore"
printf '%s\n' "node_modules/" ".agents/" >"$existing_ignore/.gitignore"
git -C "$existing_ignore" add .gitignore
git -C "$existing_ignore" commit -qm "add repository ignores"
sh "$installer" --project "$existing_ignore" >"$temporary_root/existing-ignore.out"
grep -Fqx "node_modules/" "$existing_ignore/.gitignore" ||
  fail "installer removed existing .gitignore content"
if git -C "$existing_ignore" check-ignore --no-index -q -- \
  .agents/skills/project-agent-workflow/assets/project-template/.agents/README.md; then
  fail "repository ignore rule still hides installed assets"
fi
pass "existing ignore rules are preserved while .agents becomes trackable"

relocated="$temporary_root/relocated-ignore"
init_repo "$relocated"
printf '%s\n' \
  "# project-agent-workflow: begin" \
  "!/.agents/" \
  "!/.agents/**" \
  "# project-agent-workflow: end" \
  ".agents/" >"$relocated/.gitignore"
git -C "$relocated" add .gitignore
git -C "$relocated" commit -qm "add overridden managed block"
sh "$installer" --project "$relocated" >"$temporary_root/relocated.out"
[ "$(tail -n 1 "$relocated/.gitignore")" = "# project-agent-workflow: end" ] ||
  fail "managed allow block was not moved after later ignore rules"
if git -C "$relocated" check-ignore --no-index -q -- .agents/README.md; then
  fail "relocated managed block did not make .agents trackable"
fi
pass "managed tracking block is canonicalized at the end"

malformed="$temporary_root/malformed-ignore"
init_repo "$malformed"
printf '%s\n' "keep-me/" "# project-agent-workflow: begin" "!/.agents/" \
  >"$malformed/.gitignore"
git -C "$malformed" add .gitignore
git -C "$malformed" commit -qm "add malformed managed marker"
malformed_before=$(cksum "$malformed/.gitignore")
if sh "$installer" --project "$malformed" >"$temporary_root/malformed.out" 2>&1; then
  fail "installer accepted malformed managed markers"
fi
[ "$malformed_before" = "$(cksum "$malformed/.gitignore")" ] ||
  fail "malformed .gitignore was changed"
[ ! -e "$malformed/.agents" ] || fail "malformed marker failure caused partial install"
pass "malformed tracking markers fail before mutation"

ignore_symlink="$temporary_root/ignore-symlink"
ignore_symlink_outside="$temporary_root/ignore-symlink-outside"
init_repo "$ignore_symlink"
printf '%s\n' "outside" >"$ignore_symlink_outside"
ln -s "$ignore_symlink_outside" "$ignore_symlink/.gitignore"
if sh "$installer" --project "$ignore_symlink" >"$temporary_root/ignore-symlink.out" 2>&1; then
  fail "installer accepted a symlink .gitignore"
fi
[ "$(cat "$ignore_symlink_outside")" = "outside" ] ||
  fail "installer wrote through a symlink .gitignore"
[ ! -e "$ignore_symlink/.agents" ] || fail "symlink .gitignore failure caused partial install"
pass "symlink .gitignore refusal"

symlink_repo="$temporary_root/symlink"
symlink_outside="$temporary_root/symlink-outside"
init_repo "$symlink_repo"
mkdir -p "$symlink_outside"
ln -s "$symlink_outside" "$symlink_repo/.agents"
if sh "$installer" --project "$symlink_repo" >"$temporary_root/symlink.out" 2>&1; then
  fail "installer accepted a symlink .agents target"
fi
[ -z "$(find "$symlink_outside" -mindepth 1 -print -quit)" ] ||
  fail "installer wrote through a symlink"
pass "symlink boundary refusal"

nested_symlink_repo="$temporary_root/nested-symlink"
nested_symlink_outside="$temporary_root/nested-symlink-outside"
init_repo "$nested_symlink_repo"
mkdir -p "$nested_symlink_repo/.agents" "$nested_symlink_outside"
ln -s "$nested_symlink_outside" "$nested_symlink_repo/.agents/skills"
if sh "$installer" --project "$nested_symlink_repo" \
  >"$temporary_root/nested-symlink.out" 2>&1; then
  fail "installer accepted a nested symlink in a managed path"
fi
[ -z "$(find "$nested_symlink_outside" -mindepth 1 -print -quit)" ] ||
  fail "installer wrote through a nested symlink"
[ ! -e "$nested_symlink_repo/.gitignore" ] ||
  fail "nested symlink failure changed parent .gitignore"
pass "nested symlink boundary refusal"

non_git="$temporary_root/non-git"
mkdir -p "$non_git"
if sh "$installer" --project "$non_git" >"$temporary_root/non-git.out" 2>&1; then
  fail "installer accepted a non-Git target"
fi
[ ! -e "$non_git/.agents" ] || fail "non-Git failure created .agents"
pass "non-Git target refusal"

nested="$temporary_root/nested"
init_repo "$nested"
mkdir -p "$nested/.agents"
git -C "$nested/.agents" init -q
if sh "$installer" --project "$nested" >"$temporary_root/nested.out" 2>&1; then
  fail "installer accepted nested .agents Git mode"
fi
[ ! -e "$nested/.gitignore" ] || fail "nested Git refusal changed parent .gitignore"
pass "nested .agents Git mode is rejected"

python3 "$validator" --project "$fresh" >"$temporary_root/registry-clean.out"
pass "clean registry validation"

head_commit=$(git -C "$fresh" rev-parse HEAD)
task="$fresh/.agents/tasks/active/20260904-demo.md"
cat >"$task" <<EOF
# Demo

- ID: 20260904-demo
- Status: in-progress
- Delivery gate: pending
- Owner: test
- Reviewer: test
- Created: 2026-09-04
- Updated: 2026-09-04
- Branch: main
- Base ref / merge-base: $head_commit
- Source commit: $head_commit
- Affected paths: config/**
- Architecture impact: none
- Data classification: internal
- Provenance: project-authored
- Executable: false
EOF
mkdir -p "$fresh/config"
printf '%s\n' "enabled: true" >"$fresh/config/application.yml"
python3 "$validator" --project "$fresh" --base-ref "$head_commit" \
  >"$temporary_root/registry-gate.out"
grep -Fq "covered by changed no-impact task records" "$temporary_root/registry-gate.out" ||
  fail "architecture gate test did not exercise the no-impact task path"
pass "scoped no-impact task satisfies architecture gate"

sed 's|Affected paths: config/\*\*|Affected paths: docs/**|' "$task" >"$task.invalid"
mv "$task.invalid" "$task"
if python3 "$validator" --project "$fresh" --base-ref "$head_commit" \
  >"$temporary_root/registry-gate-missing.out" 2>&1; then
  fail "architecture gate accepted an uncovered sensitive path"
fi
grep -Fq "ERROR [architecture-registry-missing]" \
  "$temporary_root/registry-gate-missing.out" ||
  fail "architecture gate did not report the uncovered path"
sed 's|Affected paths: docs/\*\*|Affected paths: config/**|' "$task" >"$task.valid"
mv "$task.valid" "$task"
pass "uncovered architecture-sensitive path is rejected"

sed 's/Status: in-progress/Status: finished/' "$task" >"$task.invalid"
mv "$task.invalid" "$task"
if python3 "$validator" --project "$fresh" >"$temporary_root/registry-invalid.out" 2>&1; then
  fail "validator accepted an invalid task state"
fi
grep -Fq "ERROR [task-state]" "$temporary_root/registry-invalid.out" ||
  fail "validator did not report task-state"
pass "invalid task state is rejected"

sed 's/Status: finished/Status: in-progress/' "$task" >"$task.valid"
mv "$task.valid" "$task"
broken_link="$fresh/.agents/reviews/broken-link.md"
printf '%s\n' '[missing](does-not-exist.md)' >"$broken_link"
if python3 "$validator" --project "$fresh" >"$temporary_root/broken-link.out" 2>&1; then
  fail "validator accepted a broken local link"
fi
grep -Fq "ERROR [broken-link]" "$temporary_root/broken-link.out" ||
  fail "validator did not report broken-link"
rm -f "$broken_link"
pass "broken local link is rejected"

secret_file="$fresh/.agents/reviews/secret.md"
secret_prefix="ghp_"
secret_suffix="abcdefghijklmnopqrstuvwxyz1234567890"
printf '%s%s\n' "$secret_prefix" "$secret_suffix" >"$secret_file"
if python3 "$validator" --project "$fresh" >"$temporary_root/secret.out" 2>&1; then
  fail "validator accepted high-confidence secret content"
fi
grep -Fq "ERROR [sensitive-data]" "$temporary_root/secret.out" ||
  fail "validator did not report sensitive-data"
rm -f "$secret_file"
pass "high-confidence secret content is rejected"

printf '%s: PASS: %s tests\n' "$program" "$tests_run"
