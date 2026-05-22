"""
BaseWorkflow, RetryPolicy, workflow signals, and exception types.

This module is the primary surface that workflow authors interact with.
"""
from __future__ import annotations

import django.db.utils
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Tuple, Type

from django.utils import timezone

if TYPE_CHECKING:
    from agen.models import WorkflowInstance


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkflowDefinitionError(Exception):
    """Raised at class-definition time (or signal-raise time) when a workflow
    is misconfigured — e.g. a retry_policy key points to a non-existent step,
    or a then_step target does not exist."""


class InfrastructureError(Exception):
    """Wraps transient infrastructure failures (DB connectivity, etc.).
    Celery's autoretry_for will pick this up and retry the task automatically.
    No RetryInstruction is created — this is a Celery-level retry, not a
    business-level retry."""


# ---------------------------------------------------------------------------
# WorkflowSignals  (always raised, never returned)
# ---------------------------------------------------------------------------

class WorkflowSignal(Exception):
    """Base class for all signals that suspend a workflow."""


class WaitTimer(WorkflowSignal):
    """Suspend the workflow for a fixed duration.

    Usage inside a step method::

        raise WaitTimer(hours=24, then_step="send_reminder")
    """

    def __init__(
        self,
        *,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        then_step: str,
    ) -> None:
        self.deadline = timezone.now() + timedelta(
            hours=hours, minutes=minutes, seconds=seconds
        )
        self.then_step = then_step
        super().__init__(f"WaitTimer(deadline={self.deadline}, then_step={then_step!r})")


class WaitHuman(WorkflowSignal):
    """Suspend the workflow until a human approves or rejects the task.

    Usage inside a step method::

        raise WaitHuman(title="Approve refund", assigned_role="manager", then_step="process")
    """

    def __init__(
        self,
        *,
        title: str,
        assigned_role: str = "",
        then_step: str,
    ) -> None:
        self.title = title
        self.assigned_role = assigned_role
        self.then_step = then_step
        super().__init__(f"WaitHuman(title={title!r}, then_step={then_step!r})")


# ---------------------------------------------------------------------------
# Internal retry signal  (inherits BaseException intentionally)
# ---------------------------------------------------------------------------

class _RetrySignal(BaseException):
    """Raised internally after the main transaction rolls back to trigger
    durable retry handling.

    Inherits from BaseException — not Exception — so a bare
    ``except Exception:`` in user step code cannot accidentally swallow it.
    Step code that uses ``except BaseException:`` or bare ``except:`` must
    always re-raise.
    """

    def __init__(
        self,
        step_name: str,
        attempt: int,
        policy: "RetryPolicy",
        original_exception: Exception,
    ) -> None:
        self.step_name = step_name
        self.attempt = attempt
        self.policy = policy
        self.original_exception = original_exception
        super().__init__(
            f"_RetrySignal(step={step_name!r}, attempt={attempt}, "
            f"exc={original_exception!r})"
        )


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

DEFAULT_INFRASTRUCTURE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    django.db.OperationalError,
    django.db.InterfaceError,
    django.db.DatabaseError,
    django.db.utils.OperationalError,
    django.db.utils.InterfaceError,
    django.db.utils.DatabaseError,
)


@dataclass
class RetryPolicy:
    """Configures business-level retries for a single step.

    Fields
    ------
    max_attempts:
        Total number of attempts (including the first). A value of 3 means
        the step is tried once, then retried twice on failure.
    backoff_factor:
        Multiplier applied to the delay on each retry (exponential backoff).
    initial_delay:
        Delay in seconds before the first retry.
    max_delay:
        Upper bound on the computed delay (seconds).
    retryable_exceptions:
        Tuple of exception types that trigger a retry. Defaults to
        ``(Exception,)`` — but you should always narrow this to specific
        transient errors.
    """

    max_attempts: int = 3
    backoff_factor: float = 2.0
    initial_delay: float = 1.0
    max_delay: float = 60.0
    retryable_exceptions: Tuple[Type[Exception], ...] = field(
        default_factory=lambda: (Exception,)
    )

    def compute_delay(self, attempt: int) -> float:
        """Return the delay (seconds) before the given attempt number (1-based).
        ``attempt=1`` means the second attempt (first retry).
        """
        delay = self.initial_delay * (self.backoff_factor ** (attempt - 1))
        return min(delay, self.max_delay)


# ---------------------------------------------------------------------------
# BaseWorkflow
# ---------------------------------------------------------------------------

class BaseWorkflow:
    """Base class for all workflow definitions.

    Subclass and define ``step_<name>`` methods. Each step method must return
    either a string (name of the next step, without the ``step_`` prefix) or
    ``None`` (workflow completes successfully).

    Class attributes
    ----------------
    retry_policy : dict[str, RetryPolicy]
        Maps step name (without ``step_`` prefix) to a RetryPolicy.
        Keys are validated at class-definition time.
    stalled_grace_period : timedelta
        How long a workflow may appear idle before stalled-recovery
        re-enqueues its current step. Default 5 minutes. Must be larger
        than the longest expected step execution time.
    infrastructure_exceptions : tuple
        Exception types treated as infrastructure errors. These trigger
        Celery-level retries; no RetryInstruction is created. Defaults to
        common Django/DB errors.
    """

    retry_policy: dict[str, RetryPolicy] = {}
    stalled_grace_period: timedelta = timedelta(minutes=5)
    infrastructure_exceptions: Tuple[Type[Exception], ...] = DEFAULT_INFRASTRUCTURE_EXCEPTIONS

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

        # Collect valid step names (without the step_ prefix)
        valid_steps: set[str] = {
            name[len("step_"):]
            for name in dir(cls)
            if name.startswith("step_") and callable(getattr(cls, name))
        }
        cls._valid_steps: frozenset[str] = frozenset(valid_steps)

        # Validate retry_policy keys
        for key in cls.retry_policy:
            if key not in valid_steps:
                raise WorkflowDefinitionError(
                    f"{cls.__name__}: retry_policy key {key!r} does not match "
                    f"any step method. Valid step names: {sorted(valid_steps)}"
                )

    def __init__(self, instance: "WorkflowInstance") -> None:
        self.instance = instance
        # state is a live reference into instance.state; mutations are
        # automatically reflected when instance.save() is called.
        self.state: dict = instance.state

    @classmethod
    def workflow_name(cls) -> str:
        """Return the canonical name used to register and look up this workflow."""
        return cls.__name__
