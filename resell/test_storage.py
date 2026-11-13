import gzip
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.timezone import localdate
from pokertracker.storage import R2MediaStorage
from .management.commands.backup_db import NAME_FORMAT, prune
from .models import ItemImage
from .test_views import ResellViewTestCase
from .tests import image_upload, make_item


class ProtectedMediaTests(ResellViewTestCase):
    def test_photos_only_for_resell_users(self):
        photo = ItemImage.objects.create(item=make_item(), image=image_upload("a.jpg"))
        response = self.client.get(photo.image.url)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/jpeg"))
        self.assertIn("private", response["Cache-Control"])
        b"".join(response.streaming_content)
        response.close()
        outsider = get_user_model().objects.create_user(username="sam", password="pw")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(photo.image.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(photo.image.url).status_code, 302)

    def test_photos_go_in_a_folder_per_day_named_by_sku(self):
        item = make_item(sku="A16-1047")
        photo = ItemImage.objects.create(item=item, image=image_upload("IMG 1234.HEIC.jpeg"))
        self.assertEqual(photo.image.name, f"{localdate():%Y-%m-%d}/A16-1047_IMG_1234.HEIC.jpg")
        legacy = ItemImage.objects.create(item=make_item(), image=image_upload("b.jpg"))
        self.assertEqual(legacy.image.name, f"{localdate():%Y-%m-%d}/b.jpg")

    def test_missing_file_and_escaping_the_folder(self):
        self.assertEqual(self.client.get("/media/2026-10-26/nope.jpg").status_code, 404)
        self.assertIn(self.client.get("/media/../db.sqlite3").status_code, (400, 404))


@override_settings(R2_ENDPOINT_URL="https://account.r2.cloudflarestorage.com", R2_BUCKET="bucket",
                   R2_ACCESS_KEY_ID="id", R2_SECRET_ACCESS_KEY="secret")
class R2MediaStorageTests(SimpleTestCase):
    def test_urls_go_through_the_protected_view_not_the_bucket(self):
        storage = R2MediaStorage()
        self.assertEqual(storage.url("2026-10-26/A01-1001_coat.jpg"), "/media/2026-10-26/A01-1001_coat.jpg")
        self.assertEqual(storage.location, "resell-images")
        self.assertFalse(storage.file_overwrite)


class BackupTests(TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.database = self.folder / "db.sqlite3"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE t (x)")
            db.execute("INSERT INTO t VALUES (42)")
        db.close()
        self.bucket = FileSystemStorage(location=self.folder / "bucket")
        patches = [mock.patch("resell.management.commands.backup_db.backup_storage", return_value=self.bucket),
                   mock.patch("resell.management.commands.backup_db.database_path", return_value=self.database)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def backup(self, *args):
        out = StringIO()
        call_command("backup_db", *args, stdout=out)
        return out.getvalue()

    def test_backup_is_a_working_compressed_copy(self):
        self.assertIn("Backed up to resell-backups/db-", self.backup())
        [name] = self.bucket.listdir("")[1]
        restored = self.folder / "restored.sqlite3"
        with self.bucket.open(name) as packed, gzip.open(packed) as raw, open(restored, "wb") as out:
            shutil.copyfileobj(raw, out)
        db = sqlite3.connect(restored)
        self.assertEqual(db.execute("SELECT x FROM t").fetchone(), (42,))
        db.close()

    def test_skips_when_nothing_changed_or_too_soon(self):
        self.backup()
        self.assertEqual(self.backup("--if-changed"), "")
        later = time.time() + 5
        os.utime(self.database, (later, later))  # a change after the backup
        self.assertEqual(self.backup("--if-changed", "--min-minutes", "60"), "")  # but the last one was just now
        self.assertIn("Backed up", self.backup("--if-changed"))

    def test_without_r2_it_does_nothing(self):
        with mock.patch("resell.management.commands.backup_db.backup_storage", return_value=None):
            self.assertIn("isn't set up", self.backup())

    def test_old_backups_are_thinned_out(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        ages = [timedelta(hours=1), timedelta(hours=5), timedelta(days=1, hours=3),  # last 2 days: keep all
                timedelta(days=5, hours=1), timedelta(days=5, hours=6),  # same day: keep the later one
                timedelta(days=100)]  # too old
        for age in ages:
            self.bucket.save((now - age).strftime(NAME_FORMAT), ContentFile(b"x"))
        self.bucket.save("notes.txt", ContentFile(b"not a backup"))
        self.assertEqual(prune(self.bucket, now), 2)
        kept = sorted(self.bucket.listdir("")[1])
        expected = sorted([(now - age).strftime(NAME_FORMAT) for age in ages[:3]]
                          + [(now - ages[3]).strftime(NAME_FORMAT), "notes.txt"])
        self.assertEqual(kept, expected)

    def test_newest_backup_is_never_deleted(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        only = (now - timedelta(days=400)).strftime(NAME_FORMAT)
        self.bucket.save(only, ContentFile(b"x"))
        self.assertEqual(prune(self.bucket, now), 0)
