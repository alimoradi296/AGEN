# Contributing to Agen

---

## Setting Up Locally

**Requirements:** Python 3.10+, PostgreSQL 14+, Redis 7+

```bash
git clone https://github.com/alimoradi296/AGEN.git
cd agen
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Docker (recommended)

Copy the sample compose file and adjust it for your environment:

```bash
cp docker-compose.sample.yml docker-compose.yml
# Edit docker-compose.yml — set your image or build target
docker compose up -d
docker compose exec web python manage.py migrate
```

---

## Running Tests

Tests require a running PostgreSQL instance. With Docker:

```bash
docker compose exec web python -m pytest tests/ -v
```

Without Docker (set up a local `agen_test` database first):

```bash
createdb agen_test
python -m pytest tests/ -v
```

The test suite has two layers:

| Path | What it tests | Needs DB | Needs Celery |
|---|---|---|---|
| `tests/functional/` | `run_workflow_sync` helper, step logic | Yes | No |
| `tests/integration/` | Full engine: locking, outbox, timers, retries, human tasks | Yes | No |

All 27 tests must pass before submitting a PR.

---

## Code Style

```bash
ruff check .
black --check .
```

Fix automatically:

```bash
ruff check --fix .
black .
```

---

## Project Structure

```
agen/                   ← the library (this is what gets installed)
  workflow.py           ← BaseWorkflow, RetryPolicy, WaitTimer, WaitHuman
  models.py             ← all 6 DB models
  engine.py             ← execution helpers (_apply_signal, _fail_step, etc.)
  tasks.py              ← execute_workflow_step Celery shared_task
  registry.py           ← @register decorator
  human.py              ← resolve_human_task()
  testing.py            ← run_workflow_sync() test helper
  signals.py            ← Django signals
  management/commands/  ← 7 management commands

demo_project/           ← demo Django project (not the library)
tests/                  ← test suite
```

---

## Key Design Rules

Before changing core execution code, read these. They exist for correctness reasons:

1. **Database is source of truth.** Never rely on broker persistence for correctness.
2. **Every instance mutation uses `select_for_update()` inside `transaction.atomic()`.**
3. **`_RetrySignal` inherits `BaseException`.** Never change this to `Exception`.
4. **`RetryInstruction.backoff_config` must be JSON-serializable.** Do not store exception classes.
5. **`_notify_outbox()` uses `transaction.on_commit`.** The NOTIFY fires after commit, not during.
6. **Phase 0 claim is a separate committed transaction.** Never merge it with Phase 1.

---

## Submitting a Pull Request

1. Fork the repo and create a branch from `main`.
2. Make your changes. Add or update tests as needed.
3. Run the full test suite — all 27 tests must pass.
4. Run `ruff` and `black`.
5. Open a PR with a clear description of what you changed and why.

**For new features:** open an issue first describing the production problem you're solving. Features without a real use case are unlikely to be merged. See the roadmap for planned work.

**For bug fixes:** include a test that reproduces the bug before your fix.

---

## Reporting Bugs

Open a GitHub issue with:

1. What you expected to happen.
2. What actually happened (include logs if possible).
3. Steps to reproduce.
4. Agen version, Django version, Python version.
