"""
Structured logging context for Agen.

Injects workflow_id and step_name into every log record produced while a
workflow step is executing.  Use set_workflow_context() at the start of a
task and clear_workflow_context() in a finally block.

Two integration modes:

1. **Zero-config (default):** Call install_log_record_factory() once at
   startup (done automatically via AgenConfig.ready()).  This patches the
   global LogRecord factory so *every* record gets workflow_id and step_name
   attributes — even from loggers created after app startup.

2. **Handler-level filter (explicit):** Add AgenContextFilter to any handler
   that should include the attributes.  Same result, more targeted.

Log format example (add to Django LOGGING config)::

    '%(asctime)s %(name)s [%(workflow_id)s %(step_name)s] %(levelname)s %(message)s'
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

_workflow_id: ContextVar[str] = ContextVar("workflow_id", default="")
_step_name: ContextVar[str] = ContextVar("step_name", default="")


class AgenContextFilter(logging.Filter):
    """Logging filter that injects workflow_id and step_name into records.

    Add to any handler to include agen context in that handler's output.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.workflow_id = _workflow_id.get("")
        record.step_name = _step_name.get("")
        return True


_original_factory = logging.getLogRecordFactory()
_factory_installed = False


def install_log_record_factory() -> None:
    """Patch the global LogRecord factory to inject agen context attributes.

    Called once by AgenConfig.ready(). Idempotent — safe to call multiple times.
    """
    global _factory_installed, _original_factory
    if _factory_installed:
        return

    _original_factory = logging.getLogRecordFactory()

    def _agen_factory(*args, **kwargs):
        record = _original_factory(*args, **kwargs)
        record.workflow_id = _workflow_id.get("")
        record.step_name = _step_name.get("")
        return record

    logging.setLogRecordFactory(_agen_factory)
    _factory_installed = True


def set_workflow_context(workflow_id: str, step_name: str) -> None:
    _workflow_id.set(workflow_id)
    _step_name.set(step_name)


def clear_workflow_context() -> None:
    _workflow_id.set("")
    _step_name.set("")
