# Agen — Implementation Status

**Last updated:** 2026-07-17
**Version:** 0.1.0 (Phase 1 + Phase 2 implemented)
**PRD reference:** `AGEN/AGEN PRD.md` (v2.8 Final)

---

## Overall Status: v0.1 Core Complete + Phase 1 Stabilisation + Phase 2 Observability

All PRD Phase 1 (Core Engine), Phase 2 (Retries & Timers), and Phase 3 (Human Tasks & Signals) work is implemented and verified end-to-end in Docker.

**Roadmap Phase 1 (Stabilisation):** Complete — hardened dispatcher, improved error messages, enhanced `agen_list_stuck`, integration test scaffold.

**Roadmap Phase 2 (Observability):** Complete — structured logging with `workflow_id`/`step_name` context, `agen_list_stuck --format json` and `--workflow` filter, optional Prometheus metrics endpoint.

---

## Component Checklist

### Data Models (`agen/models.py`)

| Model | Status | Notes |
|---|---|---|
| `WorkflowInstance` | Done | UUID PK, all 7 status choices, index on `(status, updated_at)` |
| `StepExecution` | Done | attempt tracking, input_state snapshot, error capture |
| `Timer` | Done | index on `deadline` |
| `HumanTask` | Done | `then_step`, `resolved_by`, `resolved_at` |
| `OutboxMessage` | Done | `extra` JSONField for retry kwargs, index on `(dispatched, created_at)` |
| `RetryInstruction` | Done | `error` field, `created_at` for stalled-recovery queries, index on `(retry_at, processed)` |

Migration: `agen/migrations/0001_initial.py` — applied and verified.

---

### Workflow Engine

| Component | File | Status | Notes |
|---|---|---|---|
| `BaseWorkflow` | `workflow.py` | Done | `__init_subclass__` compile-time validation of `retry_policy` keys |
| `RetryPolicy` | `workflow.py` | Done | `compute_delay()` with exponential backoff + max cap |
| `WaitTimer` | `workflow.py` | Done | Raises, never returned; `deadline` computed at raise time |
| `WaitHuman` | `workflow.py` | Done | `then_step` validated at runtime by runner |
| `_RetrySignal` | `workflow.py` | Done | Inherits `BaseException` — cannot be swallowed by `except Exception:` |
| `InfrastructureError` | `workflow.py` | Done | Triggers Celery `autoretry_for` |
| `WorkflowDefinitionError` | `workflow.py` | Done | Raised at class definition or signal-raise time |
| Workflow registry | `registry.py` | Done | `@register` decorator + `get_workflow_class()` |

---

### Core Execution (`agen/tasks.py` + `agen/engine.py`)

| Feature | Status | Notes |
|---|---|---|
| Two-phase claim (Phase 0) | Done | `RetryInstruction` claimed in separate committed tx before step runs |
| Main step transaction (Phase 1) | Done | `select_for_update` on instance, idempotency guards |
| Terminal state guard | Done | Skips if `completed` / `failed` |
| Step mismatch guard | Done | Skips if `current_step != step_name` |
| `WorkflowSignal` handling | Done | `_apply_signal` persists `Timer` or `HumanTask` atomically |
| `then_step` runtime validation | Done | Checked before `_apply_signal` |
| Infrastructure error wrapping | Done | `_is_infrastructure_exception` converts to `InfrastructureError` |
| Business retry (`_RetrySignal`) | Done | Raised inside tx → tx rolls back → `_handle_retry_signal` in new tx |
| Retry exhaustion | Done | `_fail_step` + `_fail_workflow` on last attempt |
| Step success advance | Done | `_complete_step` creates outbox message and fires signals |
| `_notify_outbox` | Done | `transaction.on_commit` fires `pg_notify('agen_outbox', '')` |
| JSON-safe backoff config | Done | Exception types excluded from `RetryInstruction.backoff_config` |

---

### Management Commands

| Command | File | Status | Schedule |
|---|---|---|---|
| `agen_dispatcher` | `management/commands/agen_dispatcher.py` | Done | Long-running process |
| `agen_sweep` | `management/commands/agen_sweep.py` | Done | Every 30s |
| `agen_tick` | `management/commands/agen_tick.py` | Done | Every 30s |
| `agen_process_retries` | `management/commands/agen_process_retries.py` | Done | Every 30s |
| `agen_recover_stalled` | `management/commands/agen_recover_stalled.py` | Done | Every 1 min |
| `agen_cleanup` | `management/commands/agen_cleanup.py` | Done | Daily |
| `agen_list_stuck` | `management/commands/agen_list_stuck.py` | Done | On-demand |
| `agen_collect_metrics` | `management/commands/agen_collect_metrics.py` | Done (optional) | Every 30s (requires `agen[metrics]`) |

**Dispatcher (Phase 1 hardened):** Uses a dedicated `agen/pg_listener.py` psycopg2 connection for LISTEN/NOTIFY, completely separate from Django's ORM pool. Reconnects automatically on `psycopg2.OperationalError`.

---

### Human Task API (`agen/human.py`)

| Feature | Status | Notes |
|---|---|---|
| `resolve_human_task(task_id, approved, resolved_by)` | Done | Idempotent; locks task then instance (consistent order) |
| Approve path | Done | Inserts outbox message for `then_step`, notifies dispatcher |
| Reject path | Done | Sets workflow `failed`, fires `workflow_failed` signal |
| Double-resolution guard | Done | Raises `ValueError` if task not `pending` |
| Wrong-state guard | Done | Raises `ValueError` if instance not `waiting_human` |

---

### Django Signals (`agen/signals.py`)

| Signal | Status |
|---|---|
| `workflow_step_started` | Done |
| `workflow_step_succeeded` | Done |
| `workflow_step_failed` | Done |
| `workflow_suspended` | Done |
| `workflow_completed` | Done |
| `workflow_failed` | Done |

---

### Testing

| Component | Status | Notes |
|---|---|---|
| `run_workflow_sync` helper | Done | `agen/testing.py` — no Celery, no outbox |
| Functional test suite | Done | `tests/functional/test_workflow_sync.py` — 10/10 passing |
| Integration test scaffold | Done | `tests/integration/conftest.py` — Celery fixture scaffold |
| Integration test suite | Done | `tests/integration/test_engine.py` — full engine coverage (no broker needed) |

**Functional test coverage:**
- Sequential flow
- Branching (left/right based on state)
- `WaitTimer` raises to caller
- `WaitHuman` raises to caller
- Failing step propagates exception
- `StepExecution` records created per step
- Initial state passed through
- Mock step substitution
- Compile-time validation (`WorkflowDefinitionError` on bad retry key)

**Integration test coverage (`test_engine.py`):**
- Step advances workflow and creates outbox message
- `StepExecution` record created on success
- Full linear flow completes
- Terminal state guard (no-op on completed workflow)
- Step mismatch guard
- `WaitTimer` suspension + `agen_tick` fires expired timer
- `agen_tick` ignores future timers
- `WaitHuman` suspension + approve/reject resolution
- Double-resolve raises `ValueError`
- Business retry creates `RetryInstruction` + outbox
- `agen_process_retries` enqueues ready retries
- Retry exhaustion fails workflow
- `agen_recover_stalled` re-enqueues stalled workflows

---

### Infrastructure & Deployment

| Component | Status | Notes |
|---|---|---|
| `docker-compose.yml` | Done | db, redis, web, worker, dispatcher, beat |
| Demo project | Done | `demo_project/` — full Django + Celery wiring |
| `UserOnboarding` demo workflow | Done | timer + human approval + optional retry simulation |
| `start_onboarding` command | Done | `--simulate-retry` flag for retry demo |
| Django admin registrations | Done | `agen/admin.py` — all 6 models |
| Dashboard template | Done | `demo_project/demo/templates/demo/dashboard.html` |
| `pyproject.toml` | Done | Build config, optional dev extras |

---

## Bugs Found and Fixed During Implementation

| Bug | Location | Fix |
|---|---|---|
| `select_for_update` outside transaction in management commands | `agen_tick`, `agen_process_retries`, `agen_recover_stalled` | Collect PKs first (no lock), then process each inside `transaction.atomic` |
| `asdict(RetryPolicy)` serializes exception classes as non-JSON-safe types | `engine.py` | Manually serialize only scalar fields into `backoff_config` |
| Celery app not initialized in dispatcher process | `demo_project/__init__.py` | Added standard `from demo_project.celery import app as celery_app` import |
| `agen/__init__.py` imports models at module level before app registry ready | `agen/__init__.py` | Removed model-importing modules from top-level `__init__` |
| `_RetrySignal` dead code — never caught after main `transaction.atomic` | `tasks.py` | Wrapped main atomic block in outer `try/except _RetrySignal` |
| Mock step in `run_workflow_sync` not persisting state before next step | `testing.py` | Unified mock and real paths to use the same `workflow` object and `StepExecution` creation |

---

## Known Limitations (from PRD ADR-004)

**Incomplete audit trail for retried steps:** When a step fails and triggers a business retry, the `StepExecution` record created at the start of that attempt is rolled back with the main transaction. Only the final attempt (success or final failure) produces a persisted `StepExecution`. Error details for intermediate attempts are captured in `RetryInstruction.error`.

**Impact:** `StepExecution` is not a complete per-attempt log for retried steps. Do not rely on it for forensics of in-progress retries.

**Planned fix:** Roadmap Phase 4 — out-of-transaction write of a `StepExecution` record for every attempt (best-effort, does not block the retry path).

---

## Observability (Phase 2)

| Feature | File | Status | Notes |
|---|---|---|---|
| Structured logging context (`workflow_id`, `step_name`) | `agen/log.py` | Done | `AgenContextFilter` injected via `apps.py ready()` |
| Task context set/clear | `agen/tasks.py` | Done | `set_workflow_context` / `clear_workflow_context` wrap each task execution |
| `agen_list_stuck --format json` | `agen_list_stuck.py` | Done | Machine-readable output for monitoring scripts |
| `agen_list_stuck --workflow <name>` | `agen_list_stuck.py` | Done | Filter by workflow name |
| `agen_list_stuck` enhanced detail | `agen_list_stuck.py` | Done | Shows pending retries, human tasks, outbox count |
| Prometheus metrics view | `agen/metrics.py` | Done (optional) | Requires `pip install agen[metrics]` |
| `agen_collect_metrics` command | `agen/management/commands/agen_collect_metrics.py` | Done (optional) | Refreshes gauge values; run every 30s |

**Log format to add (in Django LOGGING config):**
```
'%(asctime)s %(name)s [%(workflow_id)s %(step_name)s] %(levelname)s %(message)s'
```

---

## What Is Not Yet Built (Post-v0.1 Roadmap)

| Feature | Roadmap Phase | Trigger |
|---|---|---|
| Human task escalation timeout | Phase 3 | Users hit it in production |
| `WaitHuman` creation signal (`human_task_created`) | Phase 3 | Users need email/Slack without polling |
| `WaitHuman` structured payload + `resolve_human_task(resolution_data=...)` | Phase 3 | Users need form data on approval |
| Full per-attempt `StepExecution` audit trail (ADR-004 fix) | Phase 4 | After evidence from production |
| Broker-based integration tests (real Celery + in-memory broker) | Phase 4 | After v0.1 stabilisation |
| `pause_workflow` / `resume_workflow` / `cancel_workflow` | Phase 5 | Users need it |
| History archival (`agen_archive`) | Phase 5 | Tables grow too large |
| Parallel steps / fan-out | Phase 6 | Only with a concrete real-world use case |
| Optional REST API (`pip install agen[api]`) | Phase 7 | Users need non-Django access |
