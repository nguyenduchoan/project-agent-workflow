# Context budget policy

Use progressive disclosure so routine work does not load the full registry.

| Artifact | Recommended maximum |
| --- | ---: |
| `architecture/manifest.yml` | 20 KiB |
| `architecture/branches/*.md` | 32 KiB |
| `architecture/changes/*.md` | 24 KiB |
| `architecture/flows.md` | 64 KiB |
| `tasks/active/*.md` | 32 KiB |
| `tasks/history/**/*.md` | 64 KiB |

Move long raw evidence to a project-approved artifact location. Keep its redacted
summary, source, verified date, decision, and residual risk in the registry record.

Branch records must declare `Verified at` and a positive `Stale after days`.
Validation fails when a current-branch or current-task-linked record exceeds its
threshold; unrelated active-branch and historical records warn instead. A record
exactly at the threshold remains fresh. Change records used as architecture-gate
evidence must declare their own positive threshold. Refresh and revalidate evidence
instead of extending a stale date without checking authoritative sources.
