"""
agen_list_stuck — list workflow instances that appear stuck.

Usage::

    python manage.py agen_list_stuck
    python manage.py agen_list_stuck --minutes 10
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "List workflow instances that have been idle for too long."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=5,
            help="Flag instances idle for more than this many minutes (default: 5).",
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        from django.conf import settings

        from agen.models import RetryInstruction, StepExecution, WorkflowInstance

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        threshold = timezone.now() - timedelta(minutes=options["minutes"])

        stuck = (
            WorkflowInstance.objects.using(db_alias)
            .filter(updated_at__lt=threshold)
            .exclude(status__in=list(WorkflowInstance.TERMINAL_STATUSES))
            .order_by("updated_at")
        )

        if not stuck.exists():
            self.stdout.write("No stuck workflows found.")
            return

        now = timezone.now()
        count = stuck.count()
        self.stdout.write(f"Found {count} stuck workflow(s) (idle > {options['minutes']} min):\n")

        for inst in stuck:
            idle_min = (now - inst.updated_at).total_seconds() / 60

            # Last error from the most recent failed StepExecution.
            last_se = (
                StepExecution.objects.using(db_alias)
                .filter(instance=inst, status="failed")
                .order_by("-started_at")
                .first()
            )
            last_error = ""
            if last_se and last_se.error:
                # First line of the traceback is most useful at a glance.
                last_error = last_se.error.strip().splitlines()[-1][:120]

            # Pending retry info.
            pending_retry = (
                RetryInstruction.objects.using(db_alias)
                .filter(instance=inst, processed=False)
                .order_by("-created_at")
                .first()
            )

            self.stdout.write(f"  ID          : {inst.id}")
            self.stdout.write(f"  Workflow    : {inst.workflow_name}")
            self.stdout.write(f"  Status      : {inst.status}")
            self.stdout.write(f"  Step        : {inst.current_step or '(none)'}")
            self.stdout.write(f"  Idle        : {idle_min:.1f} min  (since {inst.updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')})")
            if last_error:
                self.stdout.write(f"  Last error  : {last_error}")
            if pending_retry:
                self.stdout.write(
                    f"  Retry       : attempt {pending_retry.attempt}/{pending_retry.max_attempts}"
                    f" scheduled at {pending_retry.retry_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
            self.stdout.write("")
