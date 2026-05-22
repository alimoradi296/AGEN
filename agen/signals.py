"""
Django signals emitted by the Agen engine.

Connect to these in your application code to react to workflow events::

    from django.dispatch import receiver
    from agen.signals import workflow_completed

    @receiver(workflow_completed)
    def on_completed(sender, instance, **kwargs):
        notify_user(instance.state["user_id"])
"""
from django.dispatch import Signal

# Fired at the start of each step attempt.
# kwargs: instance, step_name, attempt
workflow_step_started = Signal()

# Fired when a step completes successfully.
# kwargs: instance, step_execution
workflow_step_succeeded = Signal()

# Fired when a step fails (all retries exhausted, or no retry policy).
# kwargs: instance, step_execution, error
workflow_step_failed = Signal()

# Fired when a workflow is suspended (timer or human task).
# kwargs: instance, signal_type ("timer" | "human")
workflow_suspended = Signal()

# Fired when a workflow reaches the "completed" terminal state.
# kwargs: instance
workflow_completed = Signal()

# Fired when a workflow reaches the "failed" terminal state.
# kwargs: instance
workflow_failed = Signal()
