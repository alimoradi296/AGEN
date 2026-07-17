"""
Optional Prometheus metrics endpoint for Agen.

Only imported when prometheus_client is installed (pip install agen[metrics]).

Add to urls.py::

    from agen.metrics import metrics_view
    urlpatterns += [path("agen/metrics", metrics_view)]

Run agen_collect_metrics every 30s via Celery Beat to refresh gauge values.
"""
from __future__ import annotations

from django.http import HttpResponse


def metrics_view(request):
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return HttpResponse(
            "prometheus_client not installed. Run: pip install agen[metrics]",
            status=503,
            content_type="text/plain",
        )
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
