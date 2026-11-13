from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("One-time move to R2: copy photos and receipts from the volume to R2. Safe to run again; "
            "files already in R2 are skipped. Nothing is deleted from the volume.")

    def handle(self, *args, **options):
        if not settings.R2_ENABLED:
            raise CommandError("R2 isn't set up yet. Set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY first.")
        root = Path(settings.MEDIA_ROOT)
        files = sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []
        copied = skipped = 0
        for path in files:
            name = path.relative_to(root).as_posix()
            if default_storage.exists(name):
                skipped += 1
                continue
            with open(path, "rb") as upload:
                saved = default_storage.save(name, File(upload))
            if saved != name:
                raise CommandError(f"{name} was saved as {saved}; stopping so nothing points at the wrong file.")
            copied += 1
        self.stdout.write(f"Copied {copied} file{'s' if copied != 1 else ''} to R2"
                          f"{f', {skipped} already there' if skipped else ''}.")
