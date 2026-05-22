"""
Global registry mapping workflow_name → BaseWorkflow subclass.

Call ``register(cls)`` (or use the decorator ``@register``) to register a
workflow.  The registry is populated at import time — make sure your workflow
modules are imported before any task tries to look them up.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agen.workflow import BaseWorkflow

_registry: dict[str, type["BaseWorkflow"]] = {}


def register(cls: type["BaseWorkflow"]) -> type["BaseWorkflow"]:
    """Register a BaseWorkflow subclass.  Can be used as a decorator::

        @register
        class MyWorkflow(BaseWorkflow):
            ...
    """
    name = cls.workflow_name()
    _registry[name] = cls
    return cls


def get_workflow_class(name: str) -> type["BaseWorkflow"]:
    """Look up a registered workflow class by name.

    Raises KeyError with a helpful message if not found.
    """
    try:
        return _registry[name]
    except KeyError:
        available = sorted(_registry.keys())
        raise KeyError(
            f"Workflow {name!r} is not registered. "
            f"Available workflows: {available}. "
            "Make sure the workflow module is imported before tasks run."
        ) from None
