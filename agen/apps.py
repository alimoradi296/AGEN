from django.apps import AppConfig


class AgenConfig(AppConfig):
    name = "agen"
    verbose_name = "Agen Workflow Engine"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from agen.log import install_log_record_factory

        install_log_record_factory()
