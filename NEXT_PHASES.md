# Agen — Next Phases Implementation Guide

**Current version:** 0.1.0 (core complete, verified end-to-end)
**This document:** Concrete implementation plans for Phases 1–7.

Each phase has a trigger condition. Do not start a phase until its trigger is met. The trigger exists to prevent building things nobody needs.

---

## Phase 1 — Stabilisation ✅ COMPLETE

**Trigger:** First production deployment is live and running for at least 48 hours.

**Goal:** Make v0.1 robust enough to trust. No new features.

---

### 1.1 Harden the Dispatcher (`agen/management/commands/agen_dispatcher.py`)

**Problem:** The current dispatcher uses Django's ORM connection object to access the raw psycopg2 socket. This works but is fragile — Django may swap out the underlying connection without warning.

**What to build:** A dedicated psycopg2 connection for LISTEN/NOTIFY, completely separate from Django's ORM pool.

```python
# agen/pg_listener.py  (new file)
import select
import psycopg2
import psycopg2.extensions

def make_listen_connection(dsn: str) -> psycopg2.extensions.connection:
    """Open a dedicated psycopg2 connection for LISTEN/NOTIFY.
    Must be separate from Django's ORM pool — never share it with queries.
    """
    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    return conn

def wait_for_notify(conn, timeout: float = 30.0) -> bool:
    """Block until a notification arrives or timeout expires.
    Returns True if notified, False on timeout.
    """
    r, _, _ = select.select([conn], [], [], timeout)
    if r:
        conn.poll()
        return True
    return False
```

Update `agen_dispatcher.py` to:
1. Build the DSN from `settings.DATABASES[db_alias]`.
2. Use `pg_listener.make_listen_connection(dsn)` for LISTEN.
3. Keep using Django ORM for `_drain_outbox`.
4. On `psycopg2.OperationalError`, close the listen connection and reconnect.

**Files to change:** `agen/management/commands/agen_dispatcher.py` (new helper: `agen/pg_listener.py`).

---

### 1.2 Improve Error Messages

**Problem:** `WorkflowDefinitionError` and idempotency guard log messages are terse and hard to act on.

**Changes:**

In `agen/tasks.py`, improve the idempotency skip message to include the reason:

```python
# Current:
logger.debug("Workflow %s: current_step=%r status=%r, task step=%r — skipping.", ...)

# Better (add why):
logger.info(
    "Workflow %s skipped step %r: current_step=%r status=%r "
    "(stale task, duplicate dispatch, or recovery message — all safe to ignore).",
    instance_id, step_name, instance.current_step, instance.status,
)
```

In `agen/workflow.py`, make `WorkflowDefinitionError` messages include the class name and all valid steps:

```python
raise WorkflowDefinitionError(
    f"{cls.__name__}.retry_policy contains key {key!r} which does not match "
    f"any step method (step_{key} not found). "
    f"Defined steps: {sorted(valid_steps)}. "
    f"Check for typos in the retry_policy key."
)
```

**Files to change:** `agen/tasks.py`, `agen/workflow.py`.

---

### 1.3 `agen_list_stuck` Improvements

**Problem:** Current output is a flat table with no context on why a workflow is stuck.

**What to add:**
- Last error (from most recent `StepExecution.error` or `RetryInstruction.error`)
- Number of pending retry instructions
- Number of pending human tasks
- Pending outbox messages count

```python
# In agen_list_stuck.py handle():
for inst in stuck:
    last_se = inst.steps.order_by("-id").first()
    pending_retries = inst.retry_instructions.filter(processed=False).count()
    pending_human = inst.human_tasks.filter(status="pending").count()
    pending_outbox = inst.outbox_messages.filter(dispatched=False).count()
    last_error = ""
    if last_se and last_se.error:
        last_error = last_se.error.splitlines()[-1][:60]
    # print formatted row
```

**Files to change:** `agen/management/commands/agen_list_stuck.py`.

---

### 1.4 Integration Test Skeleton

**Problem:** No integration tests exist. A production deployment should be gated on them.

**What to build:** `tests/integration/conftest.py` with Celery worker fixture, plus `tests/integration/test_retry.py` and `tests/integration/test_timer.py`.

See Phase 4 for the full integration test plan. Phase 1 just establishes the scaffold.

```python
# tests/integration/conftest.py
import pytest
from celery.contrib.pytest import celery_app, celery_worker

@pytest.fixture(scope="session")
def celery_config():
    return {
        "broker_url": "memory://",
        "result_backend": "cache+memory://",
        "task_always_eager": False,
    }
```

**New files:** `tests/integration/conftest.py`.

---

### Phase 1 Definition of Done

- [x] Dispatcher uses a dedicated psycopg2 LISTEN connection (`agen/pg_listener.py`).
- [x] `WorkflowDefinitionError` messages name the class and list valid steps.
- [x] Idempotency skip logs at INFO with a human-readable reason.
- [x] `agen_list_stuck` shows last error, pending retries, pending human tasks.
- [x] `tests/integration/conftest.py` exists with Celery worker fixture.
- [x] No new features. Zero regressions in `tests/functional/`.

---

## Phase 2 — Observability ✅ COMPLETE

**Trigger:** A developer or operator reports difficulty finding why a workflow is stuck in production.

**Goal:** Make every log line traceable to a workflow, and expose metrics for alerting.

---

### 2.1 Structured Logging with `workflow_id`

**Problem:** Log lines from `agen` don't include `workflow_id`, making it impossible to trace a single workflow through multi-worker logs.

**Approach:** A logging filter that injects a context variable. Step execution sets it via `contextvars`.

```python
# agen/log.py  (new file)
import logging
from contextvars import ContextVar

_workflow_id: ContextVar[str] = ContextVar("workflow_id", default="")
_step_name: ContextVar[str] = ContextVar("step_name", default="")

class AgenContextFilter(logging.Filter):
    def filter(self, record):
        record.workflow_id = _workflow_id.get("")
        record.step_name = _step_name.get("")
        return True

def set_workflow_context(workflow_id: str, step_name: str):
    _workflow_id.set(workflow_id)
    _step_name.set(step_name)

def clear_workflow_context():
    _workflow_id.set("")
    _step_name.set("")
```

In `tasks.py`, call `set_workflow_context(instance_id, step_name)` at the start of the task and `clear_workflow_context()` at the end (in a `finally`).

Add the filter to all agen loggers in `apps.py`:

```python
class AgenConfig(AppConfig):
    def ready(self):
        from agen.log import AgenContextFilter
        import logging
        f = AgenContextFilter()
        for name in logging.Logger.manager.loggerDict:
            if name.startswith("agen"):
                logging.getLogger(name).addFilter(f)
```

Update log format in documentation to include `%(workflow_id)s %(step_name)s`.

**New file:** `agen/log.py`. **Changed files:** `agen/tasks.py`, `agen/apps.py`.

---

### 2.2 Enhanced `agen_list_stuck`

Already planned in Phase 1.3. Phase 2 adds:
- `--format json` flag for machine-readable output (pipe to monitoring scripts).
- `--workflow` filter by workflow name.

---

### 2.3 Prometheus Metrics (Optional, `pip install agen[metrics]`)

**Approach:** A lightweight Django view that emits text-format Prometheus metrics. No dependency on `prometheus_client` in the core — only when `agen[metrics]` is installed.

```python
# agen/metrics.py  (new file, only imported if prometheus_client available)
from django.http import HttpResponse

def metrics_view(request):
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
```

Metrics to expose:

| Metric | Type | Labels |
|---|---|---|
| `agen_workflow_instances_total` | Counter | `workflow_name`, `status` |
| `agen_step_duration_seconds` | Histogram | `workflow_name`, `step_name` |
| `agen_outbox_pending` | Gauge | — |
| `agen_retry_instructions_pending` | Gauge | — |
| `agen_human_tasks_pending` | Gauge | — |

A management command `agen_collect_metrics` refreshes the gauge values (run every 30s via Beat/cron).

**New files:** `agen/metrics.py`, `agen/management/commands/agen_collect_metrics.py`.
**pyproject.toml:** add `metrics` optional extra with `prometheus-client>=0.20`.

---

### Phase 2 Definition of Done

- [x] Every `agen` log line includes `workflow_id` and `step_name` when inside a task.
- [x] `agen_list_stuck --format json` works.
- [x] `pip install agen[metrics]` + URL config exposes Prometheus endpoint.
- [ ] Metrics scrape confirmed in a Docker Compose with a Prometheus container. *(deferred — no Prometheus container in current compose)*
- [x] No new features beyond this list. No OpenTelemetry yet.

---

## Phase 3 — Human Task Hardening

**Trigger:** A production user hits one of: tasks sitting unresolved for days with no alert, or needs to pass form data back through the approval.

**Goal:** Make `WaitHuman` production-grade for real approval workflows.

---

### 3.1 Escalation Timeout

**Problem:** A `HumanTask` can sit pending forever with no automatic action.

**Model change:** Add `escalation_deadline` (nullable DateTimeField) and `escalation_step` (CharField) to `HumanTask`.

```python
# In models.py, add to HumanTask:
escalation_deadline = models.DateTimeField(null=True, blank=True)
escalation_step = models.CharField(max_length=100, blank=True)
```

**Signal change:** `WaitHuman` gains optional parameters:

```python
class WaitHuman(WorkflowSignal):
    def __init__(
        self,
        *,
        title: str,
        assigned_role: str = "",
        then_step: str,
        escalate_after: timedelta | None = None,
        escalation_step: str = "",
    ):
        ...
```

**New management command:** `agen_escalate_human_tasks` — runs every minute, finds `HumanTask` rows where `status=pending` and `escalation_deadline <= now`. For each:
- If `escalation_step` is set: auto-reject the task and re-enqueue at `escalation_step`.
- If no `escalation_step`: auto-reject (workflow fails).
- Fire `workflow_suspended` signal with `signal_type="escalated"`.

**New migration** required for the two new fields.

**Files:** `agen/models.py`, `agen/workflow.py`, `agen/engine.py` (update `_apply_signal`), new `agen/management/commands/agen_escalate_human_tasks.py`, new migration.

---

### 3.2 Human Task Creation Signal

**Problem:** Integrators need to send email/Slack when a task is created. Currently they must poll.

**What to add:** A new Django signal `human_task_created`, fired by `_apply_signal` inside the commit hook (so the task is guaranteed to exist in the DB when the signal fires).

```python
# In agen/signals.py, add:
human_task_created = Signal()
# kwargs: instance, human_task
```

In `agen/engine.py`, fire it via `transaction.on_commit` inside `_apply_signal`:

```python
def _after_commit():
    human_task_created.send(
        sender=WorkflowInstance,
        instance=instance,
        human_task=task,
    )
transaction.on_commit(_after_commit)
```

**Files:** `agen/signals.py`, `agen/engine.py`.

---

### 3.3 Structured Payload on `WaitHuman`

**Problem:** The approver needs context (form fields, data to review) but it's currently all in the title string.

**Model change:** Add `payload` JSONField to `HumanTask`:

```python
payload = models.JSONField(default=dict, blank=True)
```

**Signal change:** `WaitHuman` gains optional `payload`:

```python
raise WaitHuman(
    title="Approve refund",
    assigned_role="finance",
    then_step="process_refund",
    payload={"amount": 99.00, "reason": "damaged goods", "order_id": 42},
)
```

**`resolve_human_task` change:** Add optional `resolution_data` dict that gets merged into `instance.state`:

```python
def resolve_human_task(task_id, approved, resolved_by="", resolution_data=None):
    ...
    if resolution_data:
        instance.state.update(resolution_data)
```

This is additive — existing callers without `resolution_data` are unaffected.

**New migration** required for `payload` field.

**Files:** `agen/models.py`, `agen/workflow.py`, `agen/human.py`, `agen/engine.py`, new migration.

---

### Phase 3 Definition of Done

- [ ] `WaitHuman(escalate_after=timedelta(hours=24), escalation_step="fallback")` works end-to-end.
- [ ] `human_task_created` signal fires after commit with correct `human_task` object.
- [ ] `WaitHuman(payload={...})` stores payload; `resolve_human_task(..., resolution_data={...})` merges into state.
- [ ] New migrations applied cleanly.
- [ ] Existing `resolve_human_task` callers without new args are unaffected.
- [ ] Integration test covers escalation timeout.

---

## Phase 4 — Audit Trail Fix (ADR-004 Resolution)

**Trigger:** A production user reports they cannot see what happened during a failed retry sequence because `StepExecution` is missing the intermediate attempts.

**Goal:** Every attempt produces a committed `StepExecution` record, even failed ones.

This is the most impactful correctness fix after v0.1. The current behaviour contradicts the "complete audit log" claim in the PRD executive summary.

---

### 4.1 Out-of-Transaction `StepExecution` Write

**Design constraint:** The fix must not change the main execution transaction. The failed-attempt write must be best-effort — if it fails, the retry path is not blocked.

**Approach:** After the `_RetrySignal` is caught (main transaction has rolled back), write a `StepExecution` record in a short separate transaction before calling `_handle_retry_signal`.

In `tasks.py`, update the `except _RetrySignal` handler:

```python
except _RetrySignal as retry_sig:
    # Main transaction has rolled back.
    # Best-effort: write a StepExecution for this failed attempt.
    _record_failed_attempt(
        instance_id=instance_id,
        step_name=retry_sig.step_name,
        attempt=retry_sig.attempt,
        exc=retry_sig.original_exception,
        started_at=step_started_at,  # captured before the transaction
    )
    _handle_retry_signal(self, retry_sig, instance_id)
```

New helper in `engine.py`:

```python
def _record_failed_attempt(instance_id, step_name, attempt, exc, started_at):
    """Write a StepExecution for a failed attempt that was rolled back.
    Best-effort: silently swallows any DB error so the retry path is not blocked.
    """
    import traceback
    try:
        with transaction.atomic():
            StepExecution.objects.create(
                instance_id=instance_id,
                step_name=step_name,
                attempt=attempt,
                status=StepExecution.STATUS_FAILED,
                input_state={},   # rolled back — not available
                error="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                started_at=started_at,
                finished_at=timezone.now(),
            )
    except Exception:
        logger.warning(
            "Failed to record failed attempt for %s step %r attempt %d",
            instance_id, step_name, attempt, exc_info=True,
        )
```

Note: `input_state` will be empty for rolled-back attempts because the snapshot was taken inside the rolled-back transaction. This is acceptable — the error text is the important part. A future improvement could snapshot state before the transaction begins.

**Capture `step_started_at`:** In `tasks.py`, capture `started_at = timezone.now()` before entering the main `transaction.atomic()` block, and pass it through `_RetrySignal`.

---

### 4.2 Integration Tests

**Trigger:** Same as Phase 4 overall.

Full integration test suite using real Celery with in-memory broker. Each test creates a real DB instance, runs the workflow with actual task execution, and asserts on outcomes.

**Test file:** `tests/integration/test_retry.py`

```python
@pytest.mark.django_db(transaction=True)
def test_retry_creates_step_executions(celery_worker):
    """Each failed attempt should produce a StepExecution record."""
    # Setup: workflow with a step that fails twice then succeeds
    # Assert: 3 StepExecution records (2 failed, 1 succeeded)
    ...
```

**Tests to write (minimum):**

| Test | File |
|---|---|
| Business retry: 2 failures + 1 success = 3 StepExecution records | `test_retry.py` |
| Retry exhaustion: workflow ends `failed` after max_attempts | `test_retry.py` |
| Infrastructure error: OperationalError triggers Celery retry, not business retry | `test_retry.py` |
| Concurrent Phase 0 claim: two workers race, only one executes | `test_retry.py` |
| Timer expiry: freezegun advances time, agen_tick fires, step executes | `test_timer.py` |
| Human task approve: workflow resumes at then_step | `test_human.py` |
| Human task reject: workflow fails | `test_human.py` |
| Human task double-resolve: second call raises ValueError | `test_human.py` |
| Outbox dispatch: step completes, Celery task received by worker | `test_dispatch.py` |
| Stalled recovery: simulate lost task, recovery re-enqueues after grace period | `test_recovery.py` |
| Missing step method: workflow fails with clear error | `test_engine.py` |

**Files:** `tests/integration/test_retry.py`, `tests/integration/test_timer.py`, `tests/integration/test_human.py`, `tests/integration/test_dispatch.py`, `tests/integration/test_recovery.py`, `tests/integration/test_engine.py`.

---

### Phase 4 Definition of Done

- [ ] Every failed attempt (not just the last) produces a `StepExecution` row with `status=failed` and the exception traceback.
- [ ] The retry path is not affected if the `_record_failed_attempt` write fails.
- [ ] All integration tests pass against a real PostgreSQL database.
- [ ] `tests/functional/` still 10/10 green.
- [ ] `IMPLEMENTATION_STATUS.md` updated: ADR-004 marked resolved.

---

## Phase 5 — Operational Controls

**Trigger:** A production user needs to intervene in a running workflow without editing the database directly.

**Goal:** `pause`, `resume`, `cancel` via the Python API. History archival to keep main tables lean.

---

### 5.1 New Status Values

Add two new statuses to `WorkflowInstance`:

```python
STATUS_PAUSED = "paused"
STATUS_CHOICES = [
    ...
    ("paused", "Paused"),
    # "cancelled" already exists
]
```

**New migration** required.

---

### 5.2 New API Functions (`agen/controls.py`, new file)

```python
def pause_workflow(instance_id: str, reason: str = "") -> None:
    """Freeze a workflow. Queued tasks will skip (idempotency guard)."""
    with transaction.atomic():
        instance = WorkflowInstance.objects.select_for_update().get(id=instance_id)
        if instance.status in WorkflowInstance.TERMINAL_STATUSES:
            raise ValueError(f"Cannot pause a terminal workflow ({instance.status}).")
        instance.status = WorkflowInstance.STATUS_PAUSED
        instance.state["_pause_reason"] = reason
        instance.save()

def resume_workflow(instance_id: str) -> None:
    """Resume a paused workflow at its current step."""
    with transaction.atomic():
        instance = WorkflowInstance.objects.select_for_update().get(id=instance_id)
        if instance.status != WorkflowInstance.STATUS_PAUSED:
            raise ValueError(f"Workflow is not paused (status={instance.status!r}).")
        instance.status = WorkflowInstance.STATUS_RUNNING
        instance.state.pop("_pause_reason", None)
        instance.save()
        OutboxMessage.objects.create(instance=instance, step_name=instance.current_step)
        _notify_outbox()

def cancel_workflow(instance_id: str, reason: str = "") -> None:
    """Permanently cancel a workflow. Cannot be reversed."""
    with transaction.atomic():
        instance = WorkflowInstance.objects.select_for_update().get(id=instance_id)
        if instance.status in WorkflowInstance.TERMINAL_STATUSES:
            raise ValueError(f"Workflow already in terminal state ({instance.status}).")
        instance.status = WorkflowInstance.STATUS_CANCELLED
        instance.state["_cancel_reason"] = reason
        instance.save()
```

**Idempotency guard update:** `tasks.py` must also skip if `instance.status == "paused"`:

```python
if instance.status in WorkflowInstance.TERMINAL_STATUSES | {"paused"}:
    return
```

**New signals** (add to `signals.py`):
- `workflow_paused(instance, reason)`
- `workflow_resumed(instance)`
- `workflow_cancelled(instance, reason)`

---

### 5.3 History Archival

**Problem:** `WorkflowInstance` and `StepExecution` tables grow indefinitely.

**Approach:** Mirror tables with identical schema. A management command copies terminal rows older than N days to the archive tables, then deletes them.

```python
# New models in models.py:
class ArchivedWorkflowInstance(models.Model):
    # identical fields to WorkflowInstance
    archived_at = models.DateTimeField(auto_now_add=True)

class ArchivedStepExecution(models.Model):
    # identical fields to StepExecution
    archived_at = models.DateTimeField(auto_now_add=True)
```

New command `agen_archive`:

```bash
python manage.py agen_archive --days 90
```

Moves `WorkflowInstance` rows with `status in (completed, failed, cancelled)` and `updated_at < now - 90 days` (and their `StepExecution`, `OutboxMessage`, `RetryInstruction` children) to archive tables.

**New migration** required. **New file:** `agen/management/commands/agen_archive.py`.

---

### Phase 5 Definition of Done

- [ ] `pause_workflow`, `resume_workflow`, `cancel_workflow` work with proper locking.
- [ ] Paused workflow's queued tasks skip without error.
- [ ] Resumed workflow re-enqueues current step immediately.
- [ ] `workflow_paused`, `workflow_resumed`, `workflow_cancelled` signals fire.
- [ ] `agen_archive --days 90` moves and deletes old terminal rows atomically.
- [ ] Integration tests cover pause → resume round-trip and cancel.

---

## Phase 6 — Parallel Steps

**Trigger:** A concrete production use case where sequential execution is a genuine bottleneck. Not a preference — actual evidence from a real workflow.

**Warning:** This is the most complex item on the roadmap. Do not start until the trigger is met and a full design ADR is written and reviewed.

---

### What Needs to be Designed (ADR First)

Before writing any code, write an ADR that answers all of the following:

**Fan-out:**
- How does a step signal "run these N steps in parallel"? New signal `WaitGroup(steps=[...])`? Or a step method that returns a list?
- Each branch needs its own `StepExecution` tracking. Does each branch get its own `WorkflowInstance` or a sub-record?
- How are branch results merged back into the parent state?

**Race condition prevention:**
- Two branches completing near-simultaneously will both try to write to `WorkflowInstance.state`. How is the merge serialised?
- The merge step must only execute after all branches complete. How is the "all done" condition evaluated atomically?

**Recovery:**
- If one branch stalls (worker crash), does stalled-recovery re-run just that branch or all branches?
- If a branch fails, do other branches continue or are they cancelled?

**Retry policy for parallel steps:**
- Does each branch have its own retry policy? Or does the parent workflow's policy apply?

**Proposed sketch (not committed):**

```python
# Possible API — subject to ADR:
class WaitGroup(WorkflowSignal):
    def __init__(self, *, branches: list[str], merge_step: str):
        self.branches = branches   # list of step names to run in parallel
        self.merge_step = merge_step

def step_process_items(self):
    raise WaitGroup(
        branches=["process_a", "process_b", "process_c"],
        merge_step="combine_results",
    )
```

**Do not implement this sketch.** Write the ADR first.

---

### Phase 6 Definition of Done

- [ ] Full design ADR written, covering fan-out, merge, recovery, and retry semantics.
- [ ] ADR reviewed and agreed.
- [ ] Implementation follows from the ADR — no speculative additions.
- [ ] Integration tests cover: all branches succeed, one branch fails, one branch stalls and recovers.

---

## Phase 7 — REST API

**Trigger:** A user needs to start workflows or resolve human tasks from a service that is not a Django application.

**Goal:** An optional installable Django app that exposes Agen's core operations over HTTP. The library core must never import DRF.

---

### 7.1 Installation

```bash
pip install agen[api]
```

```python
# settings.py
INSTALLED_APPS = [
    ...
    "rest_framework",
    "agen",
    "agen.api",   # new optional app
]

# urls.py
urlpatterns += [path("agen/", include("agen.api.urls"))]
```

---

### 7.2 Endpoints

| Method | URL | Action |
|---|---|---|
| `POST` | `/agen/workflows/` | Start a workflow instance |
| `GET` | `/agen/workflows/` | List instances (filterable by status, workflow_name) |
| `GET` | `/agen/workflows/{id}/` | Get instance detail + step history |
| `POST` | `/agen/human-tasks/{id}/resolve/` | Resolve a human task |
| `GET` | `/agen/human-tasks/` | List pending human tasks |

**Request/response example:**

```json
POST /agen/workflows/
{
    "workflow_name": "UserOnboarding",
    "state": {"user_id": 42}
}

201 Created
{
    "id": "abc123...",
    "workflow_name": "UserOnboarding",
    "status": "running",
    "current_step": "start",
    "created_at": "2026-05-22T10:00:00Z"
}
```

---

### 7.3 Auth

The API app has no built-in auth. Projects add their own DRF authentication classes via `REST_FRAMEWORK` settings. The `resolve_human_task` endpoint should require authentication in production — but this is the caller's responsibility, not Agen's.

---

### 7.4 File Structure

```
agen/api/
    __init__.py
    apps.py
    urls.py
    serializers.py
    views.py
```

No models — reads directly from `agen.models`.

---

### Phase 7 Definition of Done

- [ ] `pip install agen[api]` works.
- [ ] All 5 endpoints functional.
- [ ] No import of `rest_framework` in `agen` core.
- [ ] Tests in `tests/api/` using DRF's `APIClient`.
- [ ] Auth is the caller's responsibility — documented clearly.

---

## Summary Table

| Phase | Status | Trigger | Key Deliverable |
|---|---|---|---|
| 0 — v0.1 Core | **Complete** | — | Working engine, verified in Docker |
| 1 — Stabilisation | **Complete** | First prod deployment | Hardened dispatcher (`pg_listener.py`), better errors, integration test scaffold |
| 2 — Observability | **Complete** | Users can't debug prod | Structured logging (`agen/log.py`), `agen_list_stuck` upgrade, Prometheus metrics |
| 3 — Human Task Hardening | Not started | Users hit WaitHuman limits | Escalation timeout, creation signal, payload support |
| 4 — Audit Trail Fix | Not started | Users need per-attempt history | Out-of-tx StepExecution writes, full integration test suite |
| 5 — Operational Controls | Not started | Users edit DB manually | `pause/resume/cancel`, history archival |
| 6 — Parallel Steps | Not started | Real bottleneck reported | Design ADR first, then implementation |
| 7 — REST API | Not started | Non-Django callers needed | Optional `agen[api]` DRF app |

---

## Permanent Out-of-Scope

These will never be added regardless of requests:

- **YAML/JSON DSL** — workflows are Python. Adding a DSL creates a second language to maintain with no benefit for this use case.
- **Saga compensation** — rollback chains require a separate compensation model. Out of scope by design. Users implement compensation as explicit steps.
- **Built-in auth for human tasks** — the caller is trusted. Auth is the application's responsibility.
- **Multi-tenant isolation** — all instances share tables. Tenant isolation belongs in the application layer.
- **Kubernetes operator or Helm chart** — the target deployment is Docker Compose.
- **Admin dashboard UI** — users register their own `ModelAdmin`. A dashboard belongs in a separate package once demand exists.
