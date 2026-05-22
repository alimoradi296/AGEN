from django.apps import AppConfig


class DemoConfig(AppConfig):
    name = "demo_project.demo"
    label = "demo"
    verbose_name = "Demo"

    def ready(self):
        # Import workflows so they register themselves on startup.
        import demo_project.demo.workflows  # noqa: F401
