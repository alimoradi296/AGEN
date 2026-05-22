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

        from agen.models import WorkflowInstance

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        threshold = timezone.now() - timedelta(minutes=options["minutes"])

        stuck = (
            WorkflowInstance.objects.using(db_alias)
            .filter(
                updated_at__lt=threshold,
            )
            .exclude(status__in=list(WorkflowInstance.TERMINAL_STATUSES))
            .order_by("updated_at")
        )

        if not stuck.exists():
            self.stdout.write("No stuck workflows found.")
            return

        self.stdout.write(
            f"{'ID':<38}  {'workflow':<30}  {'status':<16}  "
            f"{'step':<20}  {'idle (min)'}"
        )
        self.stdout.write("-" * 120)

        now = timezone.now()
        for inst in stuck:
            idle = (now - inst.updated_at).total_seconds() / 60
            self.stdout.write(
                f"{str(inst.id):<38}  {inst.workflow_name:<30}  "
                f"{inst.status:<16}  {inst.current_step:<20}  {idle:.1f}"
            )
