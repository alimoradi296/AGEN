"""
Integration test configuration.

These tests hit a real PostgreSQL database and execute workflow steps
directly (no broker). The conftest here is intentionally minimal — the
Celery worker fixture is left as a scaffold for future broker-based tests.
"""
import pytest


@pytest.fixture(scope="session")
def celery_config():
    return {
        "broker_url": "memory://",
        "result_backend": "cache+memory://",
        "task_always_eager": False,
    }
