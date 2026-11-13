from django.apps import AppConfig


class ResellConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'resell'

    def ready(self):
        from . import signals  # noqa: F401
