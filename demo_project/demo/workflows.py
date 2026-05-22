"""
Demo workflow: UserOnboarding

Mirrors the PRD example. Steps:
  start → send_email → (timer 10s) → remind → (human approval) → activate → done

All external calls are simulated with print statements so no real services
are needed.
"""
import logging

from agen.registry import register
from agen.workflow import BaseWorkflow, RetryPolicy, WaitHuman, WaitTimer

logger = logging.getLogger(__name__)


class _FakeAPIError(Exception):
    """Simulates a transient external API failure (used in retry demo)."""


@register
class UserOnboarding(BaseWorkflow):
    retry_policy = {
        "activate": RetryPolicy(
            max_attempts=3,
            initial_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=(_FakeAPIError,),
        )
    }

    def step_start(self):
        user_id = self.state.get("user_id", "unknown")
        logger.info("[demo] Creating account for user %s", user_id)
        # Simulate account creation
        self.state["account_id"] = f"acct_{user_id}"
        return "send_email"

    def step_send_email(self):
        logger.info("[demo] Sending welcome email to account %s", self.state["account_id"])
        # In a real workflow you'd call an email service here.
        # Simulate an incomplete profile — always wait for a short timer.
        raise WaitTimer(seconds=10, then_step="remind")

    def step_remind(self):
        logger.info("[demo] Sending reminder to user %s", self.state.get("user_id"))
        raise WaitHuman(
            title=f"Approve profile for account {self.state['account_id']}",
            assigned_role="manager",
            then_step="activate",
        )

    def step_activate(self):
        logger.info("[demo] Activating account %s", self.state["account_id"])
        # Simulate potential transient failure on first attempt.
        attempt_key = "_activate_attempts"
        attempts = self.state.get(attempt_key, 0) + 1
        self.state[attempt_key] = attempts

        if attempts == 1 and self.state.get("simulate_retry"):
            raise _FakeAPIError("Transient activation failure (simulated)")

        self.state["activated"] = True
        logger.info("[demo] Account %s activated successfully.", self.state["account_id"])
        return None
