# CLAUDE.md — Agen Developer Guide for AI Assistants

This file tells Claude Code (and other AI assistants) everything needed to work on this codebase without asking repeated questions.

---

## What This Project Is

**Agen** is a Python library that turns Django + Celery + PostgreSQL into a durable, observable workflow engine. It is a library, not a platform — no YAML DSL, no admin dashboard, no external service dependencies.

**Core constraint:** Designed for offline-first deployment. Target: single Docker Compose host with Django, Celery, Redis, PostgreSQL.

---

## Repository Layout

```
AGEN/                          ← repo root
├── AGEN/                      ← design documents (read-only)
│   ├── AGEN PRD.md            ← authoritative spec (v2.8 Final)
│   ├── AGEN DOCUMENTS.md      ← authoring guide, ops guide, ADRs, API ref
│   └── AGEN ROADMAP.md        ← post-v0.1 roadmap (evidence-gated)
│
├── agen/                      ← the library itself
│   ├── __init__.py            ← public API (no model imports at module level)
│   ├── workflow.py            ← BaseWorkflow, RetryPolicy, signals, exceptions
│   ├── registry.py            ← @register decorator, get_workflow_class()
│   ├── models.py              ← all 6 DB models
│   ├── engine.py              ← execution helpers (_apply_signal, _fail_step, etc.)
│   ├── tasks.py               ← execute_workflow_step Celery shared_task
│   ├── dispatch.py            ← dispatch_message() via current_app.send_task
│   ├── human.py               ← resolve_human_task()
│   ├── signals.py             ← Django signals
│   ├── testing.py             ← run_workflow_sync() test helper
│   ├── admin.py               ← optional Django admin registrations
│   ├── apps.py                ← AgenConfig
│   └── management/commands/   ← 7 management commands (see below)
│
├── demo_project/              ← demo Django project (NOT the library)
│   ├── __init__.py            ← imports celery app (required for Celery init)
│   ├── celery.py              ← Celery app: demo_project.celery.app
│   ├── settings.py            ← Django settings (reads env vars)
│   ├── urls.py
│   └── demo/                  ← demo app with UserOnboarding workflow
│       ├── apps.py            ← imports workflows in ready() to register them
│       ├── workflows.py       ← UserOnboarding (timer + human + retry demo)
│       └── management/commands/start_onboarding.py
│
├── tests/
│   ├── settings.py            ← pytest Django settings (postgres + redis)
│   └── functional/
│       └── test_workflow_sync.py  ← 10 tests, all passing
│
├── docker-compose.yml         ← 6 services: db, redis, web, worker, dispatcher, beat
├── manage.py
├── pyproject.toml
├── README.md
├── CLAUDE.md                  ← this file
└── IMPLEMENTATION_STATUS.md   ← what's done, what's not, bugs fixed
```

---

## Key Design Rules — Never Violate These

1. **Database is source of truth.** All state lives in PostgreSQL. Never rely on broker persistence for correctness.

2. **Every instance mutation uses `select_for_update()`.** No exceptions. This includes retry instruction claiming, human task resolution, and step execution.

3. **`select_for_update()` requires an active transaction.** Always wrap it in `transaction.atomic()`. When iterating a queryset with `select_for_update`, collect PKs first (outside a transaction), then process each inside its own `transaction.atomic`.

4. **`RetryInstruction.backoff_config` must be JSON-serializable.** Do NOT use `asdict(RetryPolicy)` — it includes `retryable_exceptions` which are exception class objects. Store only scalar fields.

5. **`_RetrySignal` inherits `BaseException`.** Never change this to `Exception`. It must not be catchable by user's `except Exception:` blocks.

6. **The library's `__init__.py` must not import Django models at module level.** Django's app registry is not ready when `INSTALLED_APPS` is being processed. Keep model imports inside functions or the `ready()` hook.

7. **Celery task uses `shared_task`, not `app.task`.** This allows the library to work without knowing which Celery app the host project uses. The host project's `__init__.py` must do `from myproject.celery import app as celery_app` to initialize the app before management commands run.

8. **`dispatch_message` uses `current_app.send_task`.** Never call `execute_workflow_step.apply_async` directly from the dispatcher/sweeper — those processes may not have the task bound to the correct app.

9. **`_notify_outbox()` uses `transaction.on_commit`.** The NOTIFY must fire after the transaction commits, not during.

10. **Phase 0 claim is a separate committed transaction.** It must commit before Phase 1 begins. Never merge them.

---

## Management Commands Reference

| Command | Purpose | When to run |
|---|---|---|
| `agen_dispatcher` | Long-running LISTEN/NOTIFY dispatcher | Always-on service |
| `agen_sweep` | Drain undispatched outbox messages | Every 30s (sweeper fallback) |
| `agen_tick` | Fire expired timers | Every 30s |
| `agen_process_retries` | Enqueue ready RetryInstructions | Every 30s |
| `agen_recover_stalled` | Re-enqueue stalled workflows | Every 1 min |
| `agen_cleanup` | Delete old dispatched outbox messages | Daily |
| `agen_list_stuck` | List non-terminal idle instances | On demand |

---

## Running the Project

```bash
# Start all services
docker compose up -d

# Run migrations
docker exec agen-web-1 python manage.py migrate

# Trigger a demo workflow
docker exec agen-web-1 python manage.py start_onboarding --user-id alice

# Trigger with retry simulation
docker exec agen-web-1 python manage.py start_onboarding --user-id bob --simulate-retry

# Manually fire timers (useful in dev/test)
docker exec agen-web-1 python manage.py agen_tick

# Check stuck workflows
docker exec agen-web-1 python manage.py agen_list_stuck

# Run functional tests
docker exec agen-web-1 python -m pytest tests/functional/ -v

# Dashboard
open http://localhost:8001/

# Django admin (user: admin / set via createsuperuser)
open http://localhost:8001/admin/
```

---

## Writing a Workflow

```python
from agen import BaseWorkflow, RetryPolicy, WaitTimer, WaitHuman
from agen.registry import register

@register
class OrderFulfillment(BaseWorkflow):
    retry_policy = {
        "charge_card": RetryPolicy(
            max_attempts=3,
            initial_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=(PaymentGatewayError,),
        )
    }

    def step_start(self):
        self.state["order_id"] = self.state["input_order_id"]
        return "validate"

    def step_validate(self):
        validate_order(self.state["order_id"])
        return "charge_card"

    def step_charge_card(self):
        charge(self.state["order_id"])
        return "ship"

    def step_ship(self):
        create_shipment(self.state["order_id"])
        raise WaitTimer(hours=24, then_step="confirm_delivery")

    def step_confirm_delivery(self):
        raise WaitHuman(title="Confirm delivery", assigned_role="ops", then_step="done")

    def step_done(self):
        mark_complete(self.state["order_id"])
        return None  # workflow completes
```

**Rules for step methods:**
- Always return a string (next step name, no `step_` prefix) or `None` (complete).
- Use `self.state` for all persistent data — it is a plain dict, saved as JSON.
- Raise `WaitTimer` or `WaitHuman` to suspend — never return them.
- Step methods must be idempotent (may be re-executed on crash recovery).
- Never store non-JSON-serialisable values in `self.state`.

**Register your workflow** by calling `@register` or `register(MyWorkflow)`. Make sure this runs at startup — put it in your app's `AppConfig.ready()` if you use `apps.py`.

---

## Starting a Workflow Instance

```python
from agen.models import OutboxMessage, WorkflowInstance
from agen.engine import _notify_outbox
from django.db import transaction

with transaction.atomic():
    instance = WorkflowInstance.objects.create(
        workflow_name="OrderFulfillment",
        status="running",
        current_step="start",
        state={"input_order_id": order.id},
    )
    OutboxMessage.objects.create(instance=instance, step_name="start")
    _notify_outbox()
```

---

## Testing

```python
# Functional test (no Celery, no outbox)
from agen.testing import run_workflow_sync

def test_order_flow(db):
    instance = run_workflow_sync("OrderFulfillment", {"input_order_id": 1})
    assert instance.status == "completed"

# Test with a mock step
def test_with_mock(db):
    def mock_charge(wf):
        wf.state["charged"] = True
        return "ship"

    instance = run_workflow_sync(
        "OrderFulfillment",
        {"input_order_id": 1},
        mock_steps={"charge_card": mock_charge},
    )
    assert instance.state["charged"] is True
```

`run_workflow_sync` raises `WaitTimer` or `WaitHuman` if a step suspends — test for it with `pytest.raises`.

---

## Exception Taxonomy

| Exception | Meaning | Who catches it |
|---|---|---|
| `InfrastructureError` | Transient DB/network failure | Celery `autoretry_for` — up to 10 retries with jitter |
| `WorkflowSignal` (`WaitTimer`, `WaitHuman`) | Intentional suspension | Engine — persists timer/task atomically |
| `_RetrySignal` | Business retry needed | Engine — caught outside main tx, creates `RetryInstruction` |
| `WorkflowDefinitionError` | Misconfigured workflow | Propagates at class-definition time or fails the workflow at runtime |

---

## Common Mistakes to Avoid

**Don't import models at the top of `agen/__init__.py`.**
Django's app registry isn't ready. Import them inside functions.

**Don't call `select_for_update()` outside `transaction.atomic()`.**
Collect PKs first, then lock inside the transaction.

**Don't use `asdict()` on `RetryPolicy` for JSON storage.**
It includes exception classes. Store only scalar fields manually.

**Don't add `except Exception:` around a whole step body.**
It won't catch `_RetrySignal` (which is `BaseException`), but it may catch infrastructure errors before the engine can wrap them. Let exceptions propagate naturally.

**Don't forget `@register` (or `register()`) on your workflow class.**
Without it, `get_workflow_class()` will raise `KeyError` and the workflow will fail immediately.

**Don't import workflow modules lazily.**
They must be imported before any task tries to execute them. Use `AppConfig.ready()`.

---

## Environment Variables (demo_project)

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | — | Must be set; use `demo_project.settings` |
| `POSTGRES_DB` | `agen` | Database name |
| `POSTGRES_USER` | `agen` | Database user |
| `POSTGRES_PASSWORD` | `agen` | Database password |
| `POSTGRES_HOST` | `db` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker + result backend |
| `AGEN_DB_ALIAS` | `default` | Django DB alias for all agen queries |
| `SECRET_KEY` | (insecure default) | Django secret key — change in production |

---

## PRD Cross-Reference

When in doubt, the authoritative spec is `AGEN/AGEN PRD.md`. Key sections:

- **Section 5.1** — Data model (exact field names and types)
- **Section 5.4** — Two-phase claim execution algorithm (exact pseudocode)
- **Section 5.5** — Retry instruction processing and fast-path
- **Section 5.6** — Outbox dispatch, sweeper, stalled recovery
- **Section 5.7** — Timer processing (`agen_tick`)
- **Section 5.8** — Human task resolution
- **Section 10** — Design decisions and critical fixes table
