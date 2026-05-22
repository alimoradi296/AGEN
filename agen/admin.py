"""
Optional Django admin registrations.

These are convenience registrations.  Projects can override them by
unregistering and re-registering with custom ModelAdmin classes.
"""
from django.contrib import admin

from agen.models import (
    HumanTask,
    OutboxMessage,
    RetryInstruction,
    StepExecution,
    Timer,
    WorkflowInstance,
)


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow_name", "status", "current_step", "created_at", "updated_at")
    list_filter = ("status", "workflow_name")
    search_fields = ("id", "workflow_name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(StepExecution)
class StepExecutionAdmin(admin.ModelAdmin):
    list_display = ("id", "instance", "step_name", "attempt", "status", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("instance__id", "step_name")
    readonly_fields = ("started_at", "finished_at")
    ordering = ("-started_at",)


@admin.register(HumanTask)
class HumanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "instance", "title", "assigned_role", "status", "resolved_by", "resolved_at")
    list_filter = ("status",)
    search_fields = ("title", "instance__id")
    readonly_fields = ("resolved_at",)


@admin.register(OutboxMessage)
class OutboxMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "instance", "step_name", "dispatched", "created_at")
    list_filter = ("dispatched",)
    ordering = ("-created_at",)


@admin.register(RetryInstruction)
class RetryInstructionAdmin(admin.ModelAdmin):
    list_display = ("id", "instance", "step_name", "attempt", "max_attempts", "retry_at", "processed")
    list_filter = ("processed",)
    ordering = ("-created_at",)


@admin.register(Timer)
class TimerAdmin(admin.ModelAdmin):
    list_display = ("id", "instance", "deadline", "step_to_resume")
    ordering = ("deadline",)
