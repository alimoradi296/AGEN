# Changelog

All notable changes to Agen are documented here.

---

## [0.1.0] — 2026-05-22

Initial release.

### Core Engine
- Two-phase atomic step execution (PRD §5.4): Phase 0 claims `RetryInstruction` in a committed transaction before Phase 1 runs the step, preventing duplicate executions on broker loss
- Transactional outbox (`OutboxMessage`) — step result and next-step dispatch are atomic; no step result is lost on worker crash
- PostgreSQL LISTEN/NOTIFY dispatcher (`agen_dispatcher`) for sub-second dispatch latency
- Sweeper fallback (`agen_sweep`) runs every 30s to drain any outbox messages the dispatcher missed

### Workflow Features
- `BaseWorkflow` — define workflows as Python classes with `step_<name>` methods
- `WaitTimer` — suspend a workflow for a configurable duration; timer is stored in PostgreSQL and survives broker restarts
- `WaitHuman` — suspend a workflow pending human approval or rejection; resolve via `resolve_human_task()`
- `RetryPolicy` — per-step business retry with exponential backoff; retry state is durable in `RetryInstruction`
- Infrastructure error detection — transient DB errors trigger Celery-level autoretry (up to 10 times with jitter); no `RetryInstruction` is created
- `stalled_grace_period` — per-workflow override for the stalled-recovery threshold

### Management Commands
| Command | Purpose |
|---|---|
| `agen_dispatcher` | Long-running LISTEN/NOTIFY dispatcher |
| `agen_sweep` | Drain undispatched outbox messages (fallback) |
| `agen_tick` | Fire expired timers |
| `agen_process_retries` | Enqueue ready `RetryInstruction`s |
| `agen_recover_stalled` | Re-enqueue stalled `running` workflows |
| `agen_cleanup` | Delete old dispatched outbox messages |
| `agen_list_stuck` | List non-terminal idle instances with last error and retry info |

### Observability
- Django signals: `workflow_step_started`, `workflow_step_succeeded`, `workflow_step_failed`, `workflow_suspended`, `workflow_completed`, `workflow_failed`
- Full Django admin registrations for all 6 models
- `agen_list_stuck` terminal output with idle time, last error, and pending retry info

### Testing
- `run_workflow_sync()` — in-process test helper, no Celery or outbox required
- 10 functional tests (`tests/functional/`)
- 17 integration tests (`tests/integration/`) covering the full execution engine

### Known Limitations
- **Incomplete audit trail for retried steps (ADR-004):** `StepExecution` records for failed retry attempts are rolled back with the main transaction. Only the final attempt produces a committed row. Intermediate error details are in `RetryInstruction.error`. Fix planned for a future release.
- **No parallel steps:** Workflows execute sequentially.
- **Single timer per workflow:** A workflow can only have one active `WaitTimer` at a time.
