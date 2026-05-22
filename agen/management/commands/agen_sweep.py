"""
agen_sweep — drain all undispatched OutboxMessage rows.

Safety-net for the LISTEN/NOTIFY dispatcher.  Run every 30 seconds via
cron or Celery Beat::

    * * * * * cd /app && python manage.py agen_sweep
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Drain all undispatched outbox messages (sweeper safety-net)."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.db import transaction

        from agen.dispatch import dispatch_message
        from agen.models import OutboxMessage

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        count = 0

        with transaction.atomic(using=db_alias):
            messages = (
                OutboxMessage.objects.using(db_alias)
                .select_for_update(skip_locked=True)
                .filter(dispatched=False)
                .select_related("instance")
                .order_by("created_at")
            )
            for msg in messages:
                dispatch_message(msg)
                msg.dispatched = True
                msg.save()
                count += 1

        if count:
            self.stdout.write(f"agen_sweep: dispatched {count} message(s).")
        else:
            self.stdout.write("agen_sweep: nothing to dispatch.")
