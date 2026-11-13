import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pokertracker.settings")
from django.core.asgi import get_asgi_application
application = get_asgi_application()
