import gzip
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from pokertracker.storage import backup_storage

NAME_FORMAT = "db-%Y-%m-%d-%H%M%S.sqlite3.gz"
NAME_RE = re.compile(r"^db-\d{4}-\d{2}-\d{2}-\d{6}\.sqlite3\.gz$")
KEEP_ALL_DAYS = 2  # every backup from the last two days…
KEEP_DAILY_DAYS = 90  # …then the last one of each day for 90 days


class Command(BaseCommand):
    help = "Copy the whole database (poker and resell) to R2, then thin out old backups."

    def add_arguments(self, parser):
        parser.add_argument("--if-changed", action="store_true",
                            help="Skip unless the database changed since the last backup.")
        parser.add_argument("--min-minutes", type=int, default=0,
                            help="Skip if the last backup is newer than this.")

    def handle(self, *args, if_changed, min_minutes, **options):
        storage = backup_storage()
        if storage is None:
            self.stdout.write("R2 isn't set up (no keys), so there's nowhere to back up to. Skipping.")
            return
        database = database_path()
        marker = database.with_name(".last-backup")  # its modified time = when the last backup started
        last = marker.stat().st_mtime if marker.exists() else 0
        if if_changed and database.stat().st_mtime <= last:
            return
        if time.time() - last < min_minutes * 60:
            return
        started = time.time()
        name = datetime.now(timezone.utc).strftime(NAME_FORMAT)
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "db.sqlite3"
            source, target = sqlite3.connect(database), sqlite3.connect(copy)
            try:
                source.backup(target)  # a consistent copy even while the app is writing
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CommandError("The database copy failed its integrity check; not uploading it.")
            finally:
                source.close()
                target.close()
            packed = copy.with_suffix(".sqlite3.gz")
            with open(copy, "rb") as raw, gzip.open(packed, "wb") as out:
                shutil.copyfileobj(raw, out)
            with open(packed, "rb") as upload:
                storage.save(name, File(upload))
        marker.touch()
        os.utime(marker, (started, started))  # changes made during the upload get the next backup
        deleted = prune(storage, datetime.now(timezone.utc))
        self.stdout.write(f"Backed up to {settings.R2_BACKUP_FOLDER}/{name}"
                          + (f", removed {deleted} old backup{'s' if deleted != 1 else ''}." if deleted else "."))


def database_path():
    return Path(settings.DATABASES["default"]["NAME"])


def taken_at(name):
    return datetime.strptime(name, NAME_FORMAT).replace(tzinfo=timezone.utc)


def prune(storage, now):
    """Keep everything from the last KEEP_ALL_DAYS, then one per day up to KEEP_DAILY_DAYS. Never the newest."""
    names = sorted(name for name in storage.listdir("")[1] if NAME_RE.match(name))
    doomed, newest_of_day = [], {}
    for name in names[:-1]:
        age = now - taken_at(name)
        if age > timedelta(days=KEEP_DAILY_DAYS):
            doomed.append(name)
        elif age > timedelta(days=KEEP_ALL_DAYS):
            day = taken_at(name).date()
            if day in newest_of_day:
                doomed.append(newest_of_day[day])  # names sort by time, so this one is newer
            newest_of_day[day] = name
    for name in doomed:
        storage.delete(name)
    return len(doomed)
