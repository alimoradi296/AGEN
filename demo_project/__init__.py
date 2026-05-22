# This is the standard pattern for initializing the Celery app in Django.
# It ensures the app is loaded when Django starts, so all management commands
# (including agen_dispatcher, agen_sweep, etc.) have a configured Celery app.
from demo_project.celery import app as celery_app  # noqa: F401

__all__ = ("celery_app",)
