import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WorkflowInstance",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("workflow_name", models.CharField(max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("waiting_timer", "Waiting for Timer"),
                            ("waiting_human", "Waiting for Human"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("current_step", models.CharField(blank=True, max_length=100)),
                ("state", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "updated_at"],
                        name="agen_workfl_status_upd_idx",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="OutboxMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbox_messages",
                        to="agen.workflowinstance",
                    ),
                ),
                ("step_name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dispatched", models.BooleanField(default=False)),
                ("extra", models.JSONField(default=dict)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["dispatched", "created_at"],
                        name="agen_outbox_disp_created_idx",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="RetryInstruction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retry_instructions",
                        to="agen.workflowinstance",
                    ),
                ),
                ("step_name", models.CharField(max_length=100)),
                ("attempt", models.PositiveSmallIntegerField()),
                ("max_attempts", models.PositiveSmallIntegerField()),
                ("retry_at", models.DateTimeField()),
                ("backoff_config", models.JSONField()),
                ("error", models.TextField(blank=True)),
                ("processed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["retry_at", "processed"],
                        name="agen_retry_at_proc_idx",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="StepExecution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="steps",
                        to="agen.workflowinstance",
                    ),
                ),
                ("attempt", models.PositiveSmallIntegerField(default=1)),
                ("step_name", models.CharField(max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("input_state", models.JSONField(default=dict)),
                ("output", models.JSONField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["instance", "step_name", "attempt"],
                        name="agen_step_inst_name_att_idx",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="Timer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "instance",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timer",
                        to="agen.workflowinstance",
                    ),
                ),
                ("deadline", models.DateTimeField()),
                ("step_to_resume", models.CharField(max_length=100)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["deadline"], name="agen_timer_deadline_idx")
                ]
            },
        ),
        migrations.CreateModel(
            name="HumanTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="human_tasks",
                        to="agen.workflowinstance",
                    ),
                ),
                ("step_name", models.CharField(max_length=100)),
                ("title", models.CharField(max_length=200)),
                ("assigned_role", models.CharField(blank=True, max_length=50)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("resolved_by", models.CharField(blank=True, max_length=150)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("then_step", models.CharField(max_length=100)),
            ],
        ),
    ]
