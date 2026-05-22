from django.shortcuts import render

from agen.models import WorkflowInstance


def dashboard(request):
    instances = WorkflowInstance.objects.order_by("-created_at")[:50]
    return render(request, "demo/dashboard.html", {"instances": instances})
