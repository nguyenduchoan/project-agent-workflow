#!/bin/sh

set -efu

program="project-agent-workflow-installer"
dry_run=0
project_argument=""

usage() {
  printf '%s\n' \
    "Usage: install.sh [--dry-run] [--project <path>]" \
    "" \
    "Install this skill and its generic workflow templates under <repo>/.agents." \
    "The target must be a Git repository. The root .gitignore is updated so" \
    "the parent repository can track .agents; no user-level state or Git hooks change."
}

info() {
  printf '%s: %s\n' "$program" "$*"
}

die() {
  printf '%s: %s\n' "$program" "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --project)
      [ "$#" -ge 2 ] || die "--project requires a path"
      [ -z "$project_argument" ] || die "--project may be specified only once"
      project_argument=$2
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument '$1'; use --help for usage"
      ;;
  esac
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
skill_source=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
template_source="$skill_source/assets/project-template"
tracking_helper="$skill_source/scripts/ensure_parent_tracking.py"
skill_manifest="$skill_source/skill-manifest.txt"

[ -f "$skill_source/SKILL.md" ] || die "missing skill entrypoint: $skill_source/SKILL.md"
[ -f "$skill_source/agents/openai.yaml" ] || die "missing skill metadata"
[ -d "$template_source/.agents" ] || die "missing project template"
[ -f "$tracking_helper" ] || die "missing parent tracking helper"
[ -f "$skill_manifest" ] || die "missing runtime manifest: $skill_manifest"

if [ -n "$project_argument" ]; then
  [ -d "$project_argument" ] || die "project path is not a directory: $project_argument"
  project_candidate=$(CDPATH= cd -- "$project_argument" && pwd -P)
else
  project_candidate=$(pwd -P)
fi

repo_raw=$(git -C "$project_candidate" rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$repo_raw" ] || die "target is not inside a Git repository: $project_candidate"
[ -d "$repo_raw" ] || die "resolved Git root is not a directory: $repo_raw"
repo_root=$(CDPATH= cd -- "$repo_raw" && pwd -P)

case "$repo_root" in
  /*) ;;
  *) die "Git root must resolve to an absolute path" ;;
esac

agents_root="$repo_root/.agents"
destination_skill_root="$agents_root/skills/project-agent-workflow"
[ ! -L "$agents_root" ] || die "refusing symlink target: $agents_root"
if [ -e "$agents_root" ] && [ ! -d "$agents_root" ]; then
  die "managed root exists but is not a directory: $agents_root"
fi
if [ -e "$agents_root/.git" ]; then
  die "nested .agents Git mode is project-specific and is not supported by this installer"
fi

assert_safe_relative_path() {
  relative_path=$1
  case "$relative_path" in
    .agents | .agents/*) ;;
    *) die "managed destination escapes .agents: $relative_path" ;;
  esac
  case "/$relative_path/" in
    */../* | */./*) die "unsafe managed destination: $relative_path" ;;
  esac
}

assert_no_destination_symlink() (
  relative_path=$1
  current_path=$repo_root
  previous_ifs=$IFS
  IFS='/'; set -- $relative_path; IFS=$previous_ifs
  for component do
    [ -n "$component" ] || die "empty path component in $relative_path"
    current_path="$current_path/$component"
    [ ! -L "$current_path" ] || die "refusing symlink in managed path: $current_path"
  done
)

check_file() {
  source_file=$1
  relative_path=$2
  destination_file="$repo_root/$relative_path"
  assert_safe_relative_path "$relative_path"
  assert_no_destination_symlink "$relative_path"
  if [ -e "$destination_file" ]; then
    [ -f "$destination_file" ] || die "managed destination is not a regular file: $destination_file"
    cmp -s "$source_file" "$destination_file" ||
      die "conflict: existing file differs and will not be overwritten: $destination_file"
  fi
}

assert_safe_source_path() {
  relative_source=$1
  case "$relative_source" in
    "" | /*) die "unsafe runtime manifest entry: $relative_source" ;;
  esac
  case "/$relative_source/" in
    */../* | */./*) die "unsafe runtime manifest entry: $relative_source" ;;
  esac
}

visit_skill_files() {
  action=$1
  while IFS= read -r relative_source || [ -n "$relative_source" ]; do
    case "$relative_source" in
      "" | \#*) continue ;;
    esac
    assert_safe_source_path "$relative_source"
    source_file="$skill_source/$relative_source"
    [ ! -L "$source_file" ] || die "runtime manifest entry is a symlink: $source_file"
    [ -f "$source_file" ] || die "runtime manifest entry is missing: $source_file"
    relative_path=".agents/skills/project-agent-workflow/$relative_source"
    "$action" "$source_file" "$relative_path"
  done <"$skill_manifest"
}

visit_template_files() {
  action=$1
  while IFS= read -r relative_source || [ -n "$relative_source" ]; do
    case "$relative_source" in
      assets/project-template/*)
        source_file="$skill_source/$relative_source"
        [ ! -L "$source_file" ] || die "template manifest entry is a symlink: $source_file"
        [ -f "$source_file" ] || die "template manifest entry is missing: $source_file"
        relative_path=${relative_source#assets/project-template/}
        "$action" "$source_file" "$relative_path"
        ;;
    esac
  done <"$skill_manifest"
}

check_existing_skill_tree() {
  [ -e "$destination_skill_root" ] || return 0
  [ ! -L "$destination_skill_root" ] || die "refusing symlink target: $destination_skill_root"
  [ -d "$destination_skill_root" ] ||
    die "installed skill root is not a directory: $destination_skill_root"
  find "$destination_skill_root" \( -type f -o -type l \) -print | LC_ALL=C sort |
    while IFS= read -r existing_path; do
      relative_existing=${existing_path#"$destination_skill_root"/}
      [ ! -L "$existing_path" ] || die "refusing symlink in installed skill: $existing_path"
      grep -F -x -q -e "$relative_existing" "$skill_manifest" ||
        die "conflict: installed skill contains a non-manifest path: $existing_path"
    done
}

# Complete conflict and symlink preflight before the first write.
check_existing_skill_tree
visit_skill_files check_file
visit_template_files check_file
python3 "$tracking_helper" --project "$repo_root" --check

describe_file() {
  source_file=$1
  relative_path=$2
  destination_file="$repo_root/$relative_path"
  if [ -f "$destination_file" ]; then
    info "unchanged $relative_path"
  else
    info "would create $relative_path"
  fi
}

if [ "$dry_run" -eq 1 ]; then
  python3 "$tracking_helper" --project "$repo_root" --dry-run
  visit_skill_files describe_file
  visit_template_files describe_file
  info "dry-run complete; no files written"
  exit 0
fi

python3 "$tracking_helper" --project "$repo_root" --apply

create_file() {
  source_file=$1
  relative_path=$2
  destination_file="$repo_root/$relative_path"
  destination_dir=$(dirname -- "$destination_file")

  assert_safe_relative_path "$relative_path"
  assert_no_destination_symlink "$relative_path"

  if [ -e "$destination_file" ]; then
    [ -f "$destination_file" ] || die "managed destination changed type during install: $destination_file"
    cmp -s "$source_file" "$destination_file" ||
      die "managed destination changed during install: $destination_file"
    info "unchanged $relative_path"
    return 0
  fi

  mkdir -p "$destination_dir"
  assert_no_destination_symlink "$relative_path"
  temporary_file=$(mktemp "$destination_dir/.project-agent-workflow.XXXXXX") ||
    die "could not allocate temporary file in $destination_dir"
  if ! cp "$source_file" "$temporary_file"; then
    rm -f "$temporary_file"
    die "could not copy $relative_path"
  fi
  case "$relative_path" in
    */install.sh | */scripts/*.sh | */scripts/*.py) chmod 755 "$temporary_file" ;;
    *) chmod 644 "$temporary_file" ;;
  esac
  if ! ln "$temporary_file" "$destination_file" 2>/dev/null; then
    rm -f "$temporary_file"
    if [ -f "$destination_file" ] && cmp -s "$source_file" "$destination_file"; then
      info "unchanged $relative_path"
      return 0
    fi
    die "could not create without overwriting: $destination_file"
  fi
  rm -f "$temporary_file"
  info "created $relative_path"
}

visit_skill_files create_file
visit_template_files create_file

installed_verifier="$agents_root/skills/project-agent-workflow/scripts/verify_install.py"
python3 "$installed_verifier" --project "$repo_root"
info "installation complete for $repo_root"
info "next: review and stage .gitignore plus .agents in the parent repository"
