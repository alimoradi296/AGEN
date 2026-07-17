# Changelog

All notable changes to Agen are documented here.

---

## [Unreleased] — Phase 1 Stabilisation + Phase 2 Observability — 2026-07-17

### Phase 1 — Stabilisation

#### Hardened Dispatcher (`agen/pg_listener.py`)
- New dedicated `psycopg2` connection for LISTEN/NOTIFY, completely separate from Django's ORM pool — Django can no longer silently swap the underlying connection
- `build_dsn()` constructs the DSN from Django's `DATABASES` entry; `make_listen_connection()` opens an autocommit LISTEN connection; `wait_for_notify()` does socket-level `select()` with a configurable timeout
- `agen_dispatcher` updated to use `pg_listener` — reconnects cleanly on `psycopg2.OperationalError` without leaking the old connection

#### Integration Test Scaffold
- `tests/integration/conftest.py` added with session-scoped Celery config fixture
- `tests/settings.py` updated to read all DB connection params from env vars (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`) — tests now work both locally and inside Docker without changes

### Phase 2 — Observability

#### Structured Logging (`agen/log.py`)
- New `AgenContextFilter` logging filter — injects `workflow_id` and `step_name` into every log record
- `install_log_record_factory()` patches the global `LogRecord` factory so all loggers (including those created after app startup) carry context attributes automatically — no per-logger configuration needed
- `AgenConfig.ready()` calls `install_log_record_factory()` on startup
- `execute_workflow_step` calls `set_workflow_context()` at the start and `clear_workflow_context()` in a `finally` block, so every log line inside a task execution carries the workflow ID and step name
- Recommended log format: `%(asctime)s %(name)s [%(workflow_id)s %(step_name)s] %(levelname)s %(message)s`

#### Enhanced `agen_list_stuck`
- `--format json` flag — machine-readable output for piping to monitoring scripts or alerting pipelines
- `--workflow <name>` flag — filter output to a single workflow type
- Each entry now includes: pending retry count, pending human task count, pending outbox message count, and next retry schedule

#### Prometheus Metrics (optional, `pip install agen[metrics]`)
- New `agen/metrics.py` — `metrics_view` Django view that serves Prometheus text-format metrics; returns a 503 with an install hint if `prometheus_client` is not installed
- New `agen_collect_metrics` management command — refreshes gauge values; run every 30s via Celery Beat or cron
- Metrics exposed: `agen_outbox_pending`, `agen_retry_instructions_pending`, `agen_human_tasks_pending`
- `pyproject.toml` gains a `metrics` optional extra: `pip install agen[metrics]`

### Tests
- All 27 tests pass (10 functional + 17 integration)

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
