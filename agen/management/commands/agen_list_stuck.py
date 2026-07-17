"""
agen_list_stuck — list workflow instances that appear stuck.

Usage::

    python manage.py agen_list_stuck
    python manage.py agen_list_stuck --minutes 10
    python manage.py agen_list_stuck --workflow OrderFulfillment
    python manage.py agen_list_stuck --format json
"""
import json

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
        parser.add_argument(
            "--workflow",
            type=str,
            default="",
            help="Filter by workflow name (exact match).",
        )
        parser.add_argument(
            "--format",
            dest="output_format",
            choices=["text", "json"],
            default="text",
            help="Output format: text (default) or json.",
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        from django.conf import settings

        from agen.models import RetryInstruction, StepExecution, WorkflowInstance

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        threshold = timezone.now() - timedelta(minutes=options["minutes"])

        qs = (
            WorkflowInstance.objects.using(db_alias)
            .filter(updated_at__lt=threshold)
            .exclude(status__in=list(WorkflowInstance.TERMINAL_STATUSES))
            .order_by("updated_at")
        )

        if options["workflow"]:
            qs = qs.filter(workflow_name=options["workflow"])

        if not qs.exists():
            if options["output_format"] == "json":
                self.stdout.write("[]")
            else:
                self.stdout.write("No stuck workflows found.")
            return

        now = timezone.now()
        rows = []

        for inst in qs:
            idle_min = (now - inst.updated_at).total_seconds() / 60

            last_se = (
                StepExecution.objects.using(db_alias)
                .filter(instance=inst, status="failed")
                .order_by("-started_at")
                .first()
            )
            last_error = ""
            if last_se and last_se.error:
                last_error = last_se.error.strip().splitlines()[-1][:120]

            pending_retries = (
                RetryInstruction.objects.using(db_alias)
                .filter(instance=inst, processed=False)
                .count()
            )
            pending_human = inst.human_tasks.filter(status="pending").count()
            pending_outbox = inst.outbox_messages.filter(dispatched=False).count()

            pending_retry = (
                RetryInstruction.objects.using(db_alias)
                .filter(instance=inst, processed=False)
                .order_by("-created_at")
                .first()
            )

            rows.append({
                "id": str(inst.id),
                "workflow_name": inst.workflow_name,
                "status": inst.status,
                "current_step": inst.current_step or "",
                "idle_minutes": round(idle_min, 1),
                "updated_at": inst.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "last_error": last_error,
                "pending_retries": pending_retries,
                "pending_human_tasks": pending_human,
                "pending_outbox": pending_outbox,
                "next_retry": (
                    {
                        "attempt": pending_retry.attempt,
                        "max_attempts": pending_retry.max_attempts,
                        "retry_at": pending_retry.retry_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    }
                    if pending_retry else None
                ),
            })

        if options["output_format"] == "json":
            self.stdout.write(json.dumps(rows, indent=2))
            return

        self.stdout.write(
            f"Found {len(rows)} stuck workflow(s) (idle > {options['minutes']} min):\n"
        )
        for r in rows:
            self.stdout.write(f"  ID              : {r['id']}")
            self.stdout.write(f"  Workflow        : {r['workflow_name']}")
            self.stdout.write(f"  Status          : {r['status']}")
            self.stdout.write(f"  Step            : {r['current_step'] or '(none)'}")
            self.stdout.write(
                f"  Idle            : {r['idle_minutes']:.1f} min  (since {r['updated_at']})"
            )
            if r["last_error"]:
                self.stdout.write(f"  Last error      : {r['last_error']}")
            self.stdout.write(f"  Pending retries : {r['pending_retries']}")
            self.stdout.write(f"  Pending human   : {r['pending_human_tasks']}")
            self.stdout.write(f"  Pending outbox  : {r['pending_outbox']}")
            if r["next_retry"]:
                nr = r["next_retry"]
                self.stdout.write(
                    f"  Next retry      : attempt {nr['attempt']}/{nr['max_attempts']}"
                    f" at {nr['retry_at']}"
                )
            self.stdout.write("")
