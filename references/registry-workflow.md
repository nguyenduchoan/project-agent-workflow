# Registry workflow

## Progressive context loading

Read only the smallest current context:

1. The active task matching the request.
2. `architecture/manifest.yml` when architecture context is relevant.
3. `architecture/branches/<branch-slug>.md` for the current branch when present.
4. A relevant section of `architecture/flows.md` and linked change records only
   when the change touches that flow.

Source code, runtime configuration, tests, and current operational evidence remain
authoritative. Mark or refresh stale registry content rather than silently relying
on it.

## Task lifecycle

Create task records from `tasks/templates/task.md` in `tasks/active`. Use one
canonical state:

- `todo`
- `in-progress`
- `blocked`
- `in-review`
- `done`
- `cancelled`

Keep lifecycle state separate from `delivery gate`: `not-applicable`, `pending`,
`passed`, or `risk-accepted`. Move completed and cancelled records to their matching
history directories; update links that referenced the previous path.

## Architecture impact

- `none`: internal implementation change without boundary, contract, runtime, or
  non-functional behavior changes.
- `documentation-only`: workflow or documentation change that does not alter the
  application architecture.
- `architecture-change`: module/runtime boundary, API/event/data contract,
  integration, migration, cache/lock, security, observability, or async lifecycle
  changes.

For `architecture-change`, update the branch record and create or update a linked
change record. For `none` or `documentation-only`, record a concrete reason and
affected paths in the active task.

## Review and closure

Before closing a task, record focused validation, skipped checks with reasons,
compatibility/security/performance/scale impact, rollout and rollback notes, and
residual risk. When the repository requires a durable review report, create it
from `reviews/templates/review-report.md` in the location and extension specified
by that repository's `AGENTS.md`.

Do not mark work complete solely because code was written or a single happy-path
test passed.
