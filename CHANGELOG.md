# Changelog

All notable changes are recorded here. Versions follow Semantic Versioning.

## 1.1.0 - Unreleased

- Disabled implicit skill invocation and documented explicit
  `$project-agent-workflow` usage.
- Added automatic architecture-base resolution, explicit opt-out, configurable
  sensitive paths, and deterministic stale-record enforcement.
- Added semantic Git metadata and safe related-task reference validation.
- Added `LIGHT`, `STANDARD`, and `STRICT` workflow modes with risk-based mode
  escalation while preserving legacy STANDARD-like task records.
- Added bounded project-specific secret patterns while making all built-in
  high-confidence checks mandatory and immutable through policy.
- Added Agent Skills compatibility metadata, manifest-assembled official
  validation in CI, focused validator tests, and release/version guidance.
- Strengthened installer manifest preflight with duplicate and traversal
  regression tests.
- Removed the undocumented `.mb` scanning suffix and added regression coverage.
- Scoped stale-record errors and checkout-relative Git checks to current work while
  preserving structural validation across active branches and history.
- Hardened architecture evidence discovery, affected-path matching, safe
  task-to-record links, confirmed/STRICT change-record requirements, and freshness
  checks without requiring task files to change in every sensitive diff.
- Added Ubuntu/macOS coverage for the portable core CI test job and aligned Python
  command and primary-branch documentation with actual behavior.
- Modularized the registry validator into Git, path/glob, policy, record,
  architecture, secret, and finding modules without changing V2 workflow rules.
- Added a generic trusted host registry with Codex and Claude Code adapters,
  interactive multi-select, repeatable `--host`, `--host all`, host listing,
  atomic multi-host preflight, and deterministic no-TTY behavior.
- Added shared host-neutral BCP-47-style language preferences, safe preservation
  and explicit update behavior, segment-aware glob semantics, and managed
  executable permission checks.

## 1.0.0 - 2026-09-04

- Added the repository-scoped Agent Skill and generic `.agents` registry template.
- Added a one-command, fail-closed installer with dry-run and idempotent reinstall.
- Added parent Git tracking rules, registry validation, package verification, and
  end-to-end security and lifecycle tests.
- Added open-source contribution, security, license, and CI metadata.
