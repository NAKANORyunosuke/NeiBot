from django.apps import AppConfig


class PanelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel"

    def ready(self):
        # Hook signals for allauth to mark staff users
        try:
            from . import signals  # noqa: F401
        except Exception:
            pass

