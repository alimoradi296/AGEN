"""
python manage.py start_onboarding [--user-id 42] [--simulate-retry]

Creates and launches a UserOnboarding workflow instance.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start a demo UserOnboarding workflow."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", default="demo_user_1")
        parser.add_argument(
            "--simulate-retry",
            action="store_true",
            help="Inject a transient failure on the activate step to demo retries.",
        )

    def handle(self, *args, **options):
        from agen.models import OutboxMessage, WorkflowInstance
        from agen.engine import _notify_outbox
        from django.db import transaction

        user_id = options["user_id"]
        simulate_retry = options["simulate_retry"]

        with transaction.atomic():
            instance = WorkflowInstance.objects.create(
                workflow_name="UserOnboarding",
                status="running",
                current_step="start",
                state={
                    "user_id": user_id,
                    "simulate_retry": simulate_retry,
                },
            )
            OutboxMessage.objects.create(instance=instance, step_name="start")
            _notify_outbox()

        self.stdout.write(
            f"Workflow instance created: {instance.id}\n"
            f"  user_id       = {user_id}\n"
            f"  simulate_retry= {simulate_retry}\n"
            f"\nWatch progress:\n"
            f"  http://localhost:8001/\n"
            f"  http://localhost:8001/admin/agen/workflowinstance/{instance.id}/change/\n"
        )
