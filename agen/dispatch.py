"""
dispatch_message — sends a single OutboxMessage as a Celery task.
"""
from __future__ import annotations

import logging

from agen.models import OutboxMessage

logger = logging.getLogger(__name__)


def dispatch_message(msg: OutboxMessage) -> None:
    """Publish a Celery task for the given OutboxMessage.

    Uses celery.current_app.send_task so the correct broker URL is always
    used regardless of which process calls this (dispatcher, sweeper, etc.).
    """
    from celery import current_app

    kwargs = dict(msg.extra) if msg.extra else {}

    current_app.send_task(
        "agen.tasks.execute_workflow_step",
        kwargs={
            "instance_id": str(msg.instance_id),
            "step_name": msg.step_name,
            **kwargs,
        },
    )

    logger.debug(
        "Dispatched task for workflow %s step %r kwargs=%r",
        msg.instance_id,
        msg.step_name,
        kwargs,
    )
