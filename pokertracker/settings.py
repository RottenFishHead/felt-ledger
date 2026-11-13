import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
# Debug pages show code and settings, so they're never on at Fly, even if DJANGO_DEBUG goes missing from fly.toml.
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1" and not os.environ.get("FLY_APP_NAME")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
if not DEBUG and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Set the DJANGO_SECRET_KEY environment variable (fly secrets set DJANGO_SECRET_KEY=...).")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]
if os.environ.get("FLY_APP_NAME"):  # set automatically on Fly machines
    ALLOWED_HOSTS.append(f"{os.environ['FLY_APP_NAME']}.fly.dev")
    CSRF_TRUSTED_ORIGINS.append(f"https://{os.environ['FLY_APP_NAME']}.fly.dev")
# Persistent data (SQLite database + uploads). On Fly this is the mounted volume, e.g. /data.
DATA_DIR = Path(os.environ.get("DJANGO_DATA_DIR", BASE_DIR))
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "poker_sessions.apps.SessionsConfig", "resell.apps.ResellConfig"
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "pokertracker.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "resell.access.resell_access",
    ]},
}]
WSGI_APPLICATION = "pokertracker.wsgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": DATA_DIR / "db.sqlite3",
    # Two gunicorn workers share one SQLite file: wait for locks instead of failing, and take write locks up front.
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = DATA_DIR / "media"
# Cloudflare R2: once both keys are set (fly secrets), uploads go to R2 and the database is backed up there.
# Without them, uploads stay in MEDIA_ROOT (same folder layout) and there are no off-site backups. Files are always served through
# protected_media (login required), never straight from the bucket.
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_MEDIA_FOLDER = "resell-images"  # photos in resell-images/2026-10-26/…, receipts in resell-images/receipts/…
R2_BACKUP_FOLDER = "resell-backups"
R2_ENABLED = all([R2_ENDPOINT_URL, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY])
if R2_ENABLED:
    STORAGES["default"] = {"BACKEND": "pokertracker.storage.R2MediaStorage"}
# Dollars per mile for resell mileage expenses (IRS standard business rate) — update each January.
# Saved trips keep the rate they were entered with.
RESELL_MILEAGE_RATE = "0.725"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Without debug, Django prints nothing about crashes. Send warnings and errors (with the traceback) to the
    # console, which is what `fly logs` shows. Page-not-found and permission warnings are left out as noise.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "{levelname} {name}: {message}", "style": "{"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
        "root": {"handlers": ["console"], "level": "WARNING"},
        "loggers": {"django.request": {"level": "ERROR"}},
    }
