"""Cloudflare R2 storage for uploaded photos/receipts and database backups. Used only when the R2 keys are set."""
from urllib.parse import urljoin
from botocore.config import Config
from django.conf import settings
from django.utils.encoding import filepath_to_uri
from storages.backends.s3 import S3Storage


def r2_options(folder):
    return {
        "endpoint_url": settings.R2_ENDPOINT_URL,
        "bucket_name": settings.R2_BUCKET,
        "access_key": settings.R2_ACCESS_KEY_ID,
        "secret_key": settings.R2_SECRET_ACCESS_KEY,
        "region_name": "auto",
        "location": folder,  # a folder in the bucket, which other projects share
        "default_acl": None,
        "file_overwrite": False,
        # Newer boto3 adds checksums R2 doesn't accept on every request; only send them when required.
        "client_config": Config(signature_version="s3v4", request_checksum_calculation="when_required",
                                response_checksum_validation="when_required"),
    }


class R2MediaStorage(S3Storage):
    """Photos and receipts live in R2, but their URLs stay /media/… so protected_media checks the login
    before handing a file over. The bucket itself is never public."""

    def __init__(self, **settings_overrides):
        super().__init__(**{**r2_options(settings.R2_MEDIA_FOLDER), **settings_overrides})

    def url(self, name, parameters=None, expire=None, http_method=None):
        return urljoin(settings.MEDIA_URL, filepath_to_uri(name))


def backup_storage():
    """Where database backups go, or None when R2 isn't set up."""
    return S3Storage(**r2_options(settings.R2_BACKUP_FOLDER)) if settings.R2_ENABLED else None
