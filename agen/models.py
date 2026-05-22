from uuid import uuid4

from django.db import models


class WorkflowInstance(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_WAITING_TIMER = "waiting_timer"
    STATUS_WAITING_HUMAN = "waiting_human"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_WAITING_TIMER, "Waiting for Timer"),
        (STATUS_WAITING_HUMAN, "Waiting for Human"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    workflow_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default=STATUS_PENDING, choices=STATUS_CHOICES)
    current_step = models.CharField(max_length=100, blank=True)
    state = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"WorkflowInstance({self.workflow_name}, {self.status}, step={self.current_step})"


class StepExecution(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name="steps"
    )
    attempt = models.PositiveSmallIntegerField(default=1)
    step_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default=STATUS_PENDING, choices=STATUS_CHOICES)
    input_state = models.JSONField(default=dict)
    output = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["instance", "step_name", "attempt"]),
        ]

    def __str__(self) -> str:
        return (
            f"StepExecution({self.instance_id}, step={self.step_name}, "
            f"attempt={self.attempt}, {self.status})"
        )


class Timer(models.Model):
    instance = models.OneToOneField(
        WorkflowInstance, on_delete=models.CASCADE, related_name="timer"
    )
    deadline = models.DateTimeField()
    step_to_resume = models.CharField(max_length=100)

    class Meta:
        indexes = [
            models.Index(fields=["deadline"]),
        ]

    def __str__(self) -> str:
        return f"Timer({self.instance_id}, deadline={self.deadline}, resume={self.step_to_resume})"


class HumanTask(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name="human_tasks"
    )
    step_name = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    assigned_role = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, default=STATUS_PENDING, choices=STATUS_CHOICES)
    resolved_by = models.CharField(max_length=150, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    then_step = models.CharField(max_length=100)

    def __str__(self) -> str:
        return f"HumanTask({self.instance_id}, {self.title}, {self.status})"


class OutboxMessage(models.Model):
    id = models.BigAutoField(primary_key=True)
    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name="outbox_messages"
    )
    step_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched = models.BooleanField(default=False)
    # carries e.g. {"retry_attempt": 2} or {"recovery": True}
    extra = models.JSONField(default=dict)

    class Meta:
        indexes = [
            # Partial index equivalent – filtered in queries via dispatched=False
            models.Index(fields=["dispatched", "created_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"OutboxMessage({self.instance_id}, step={self.step_name}, "
            f"dispatched={self.dispatched})"
        )


class RetryInstruction(models.Model):
    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name="retry_instructions"
    )
    step_name = models.CharField(max_length=100)
    attempt = models.PositiveSmallIntegerField()
    max_attempts = models.PositiveSmallIntegerField()
    retry_at = models.DateTimeField()
    backoff_config = models.JSONField()
    error = models.TextField(blank=True)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["retry_at", "processed"]),
        ]

    def __str__(self) -> str:
        return (
            f"RetryInstruction({self.instance_id}, step={self.step_name}, "
            f"attempt={self.attempt}, processed={self.processed})"
        )
