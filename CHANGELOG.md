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

## 1.0.0 - 2026-09-04

- Added the repository-scoped Agent Skill and generic `.agents` registry template.
- Added a one-command, fail-closed installer with dry-run and idempotent reinstall.
- Added parent Git tracking rules, registry validation, package verification, and
  end-to-end security and lifecycle tests.
- Added open-source contribution, security, license, and CI metadata.
