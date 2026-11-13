import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from . import locations
from .models import InventorySettings, Item
from .test_views import TODAY, ResellViewTestCase


def new_item(location="", **kwargs):
    item = Item(title="Jacket", purchase_date=TODAY - timedelta(days=5), purchase_price=D("4.00"), **kwargs)
    locations.save_new_item(item, location)
    return item


def configure(**fields):
    config = InventorySettings.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()
    return config


class BinSequenceTests(TestCase):
    def test_bins_run_like_spreadsheet_columns(self):
        names = {0: "A", 25: "Z", 26: "AA", 27: "AB", 51: "AZ", 52: "BA", 701: "ZZ", 702: "AAA"}
        for index, name in names.items():
            self.assertEqual(locations.bin_name(index), name)
            self.assertEqual(locations.bin_index(name), index)

    def test_slots_are_zero_padded(self):
        self.assertEqual(locations.normalize(" a5 "), "A05")
        self.assertEqual(locations.normalize("ab16"), "AB16")
        self.assertEqual(locations.normalize("Closet shelf"), "Closet shelf")
        self.assertIsNone(locations.parse_slot("A123"))


class AssignmentTests(TestCase):
    def test_first_item_and_26th_item(self):
        self.assertEqual(new_item().sku, "A01-1001")
        for _ in range(24):
            new_item()
        item = new_item()
        self.assertEqual(item.sku, "B01-1026")
        self.assertEqual((item.item_number, item.storage_location), (1026, "B01"))
        self.assertEqual(InventorySettings.load().next_item_number, 1027)

    def test_settings_are_always_one_row(self):
        InventorySettings.load()
        InventorySettings.load()
        self.assertEqual(list(InventorySettings.objects.values_list("pk", flat=True)), [1])

    def test_counter_starts_after_the_highest_existing_number(self):
        Item.objects.create(title="Old", purchase_date=TODAY, item_number=1500)
        self.assertEqual(InventorySettings.load().next_item_number, 1501)

    def test_shipped_slot_is_reused_with_a_new_number(self):
        items = [new_item() for _ in range(10)]
        a05 = items[4]
        a05.sale_date, a05.sale_price, a05.platform_id = TODAY, D("20"), 1
        a05.save()
        self.assertEqual(new_item().storage_location, "A11")  # sold but not shipped: still in A05
        a05.shipped_at = timezone.now()
        locations.release(a05)
        a05.save()
        item = new_item()
        self.assertEqual(item.sku, "A05-1012")
        a05.refresh_from_db()
        self.assertEqual((a05.sku, a05.storage_location), ("A05-1005", "A05"))  # kept as history

    def test_written_off_item_frees_its_slot(self):
        first = new_item()
        first.written_off_date = TODAY
        locations.release(first)
        first.save()
        self.assertEqual(new_item().storage_location, "A01")

    def test_capacity_override_disabled_bins_and_z_to_aa(self):
        configure(default_bin_capacity=2, bin_capacity_overrides={"B": 1}, disabled_bins=["C"])
        made = [new_item().storage_location for _ in range(5)]
        self.assertEqual(made, ["A01", "A02", "B01", "D01", "D02"])
        configure(default_bin_capacity=1, bin_capacity_overrides={}, disabled_bins=[])
        for letter in "CEFGHIJKLMNOPQRSTUVWXYZ":
            self.assertEqual(new_item().storage_location, f"{letter}01")
        self.assertEqual(new_item().storage_location, "AA01")

    def test_lower_capacity_keeps_items_but_stops_assigning_above_it(self):
        configure(default_bin_capacity=5)
        items = [new_item() for _ in range(5)]
        configure(default_bin_capacity=3)
        items[1].shipped_at = timezone.now()
        locations.release(items[1])
        items[1].save()
        self.assertEqual(new_item().storage_location, "A02")  # gap below the new capacity is filled
        self.assertEqual(new_item().storage_location, "B01")  # A04 and A05 stay put but aren't handed out
        self.assertEqual(Item.objects.filter(storage_location__in=["A04", "A05"]).count(), 2)

    def test_older_items_with_slot_shaped_locations_block_those_slots(self):
        Item.objects.create(title="Old", purchase_date=TODAY, storage_location="a1", sku="OLD-7")
        Item.objects.create(title="Old 2", purchase_date=TODAY, storage_location="Closet shelf")
        self.assertEqual(new_item().storage_location, "A02")

    def test_duplicate_sku_is_impossible(self):
        new_item()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(title="Copy", purchase_date=TODAY, sku="A01-1001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(title="Copy", purchase_date=TODAY, item_number=1001)

    def test_settings_validation(self):
        config = configure()
        config.bin_capacity_overrides = {"k": 12}
        with self.assertRaises(ValidationError):
            config.full_clean()
        config.bin_capacity_overrides, config.disabled_bins = {"K": 12}, ["I", "O"]
        config.full_clean()
        new_item()
        config.next_item_number = 1001
        with self.assertRaises(ValidationError):
            config.full_clean()  # the counter never goes back


class RaceTests(SimpleTestCase):
    """Two devices saving at the same moment: separate processes on one SQLite file, as on the server."""
    SAVE = ("from datetime import date; from resell.models import Item; from resell.locations import save_new_item\n"
            "for _ in range(10): save_new_item(Item(title='Racer', purchase_date=date.today()), 'A01')")

    def manage(self, data_dir, *args):
        env = {**os.environ, "DJANGO_DATA_DIR": data_dir}
        return subprocess.Popen([sys.executable, "manage.py", *args], cwd=settings.BASE_DIR, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_racing_saves_get_different_slots_and_skus(self):
        with tempfile.TemporaryDirectory() as data_dir:
            migrate = self.manage(data_dir, "migrate", "--verbosity", "0")
            self.assertEqual(migrate.wait(), 0, migrate.stderr.read())
            racers = [self.manage(data_dir, "shell", "-c", self.SAVE) for _ in range(2)]
            for racer in racers:
                self.assertEqual(racer.wait(), 0, racer.stderr.read())
            report = self.manage(data_dir, "shell", "--verbosity", "0", "-c", "from resell.models import Item\n"
                                 "print(*Item.objects.order_by('item_number').values_list('sku', flat=True))")
            skus = report.communicate()[0].split()
        self.assertEqual(skus, [f"A{slot:02d}-{1000 + slot}" for slot in range(1, 21)])


class AddItemFormTests(ResellViewTestCase):
    def form_data(self, **extra):
        return {"title": "Coat", "condition": "good", "purchase_date": TODAY.isoformat(), "purchase_price": "5", **extra}

    def test_form_shows_preview_without_reserving_anything(self):
        for _ in range(3):  # opened and abandoned three times
            response = self.client.get(reverse("resell:item-create"))
        self.assertContains(response, 'value="A01"')
        self.assertContains(response, 'value="A01-1001"')
        self.assertContains(response, "Bin full → next bin")
        self.assertEqual(InventorySettings.load().next_item_number, 1001)
        self.post("item-create", self.form_data(storage_location="A01"))
        self.assertEqual(Item.objects.get().sku, "A01-1001")

    def test_sku_typed_into_the_form_is_ignored(self):
        self.post("item-create", self.form_data(storage_location="A01", sku="MINE-1"))
        self.assertEqual(Item.objects.get().sku, "A01-1001")

    def test_location_override(self):
        response = self.post("item-create", self.form_data(storage_location="c7"))
        self.assertContains(response, "as C07-1001")
        self.assertEqual(Item.objects.get().storage_location, "C07")

    def test_bad_overrides_are_refused(self):
        configure(disabled_bins=["D"])
        new_item("B02")
        for location, message in [("B02", "already in B02"), ("A26", "slots 01 to 25"), ("D01", "switched off"),
                                  ("Closet", "bin letter and slot number"), ("A00", "slots 01 to 25")]:
            response = self.post("item-create", self.form_data(storage_location=location))
            self.assertContains(response, message)
        self.assertEqual(Item.objects.count(), 1)

    def test_slot_taken_by_another_device_meanwhile(self):
        self.client.get(reverse("resell:item-create"))  # this phone sees A01-1001
        new_item()  # another device saves first
        response = self.post("item-create", self.form_data(storage_location="A01", offered_location="A01"))
        self.assertContains(response, "A01 was just filled")
        self.assertEqual(Item.objects.order_by("pk").last().sku, "A02-1002")

    def test_blank_location_gets_the_next_open_slot(self):
        self.post("item-create", self.form_data(storage_location=""))
        self.assertEqual(Item.objects.get().sku, "A01-1001")

    def test_check_and_next_bin_endpoints(self):
        new_item("A01")
        check = self.client.get(reverse("resell:location-check"), {"location": "a1"}).json()
        self.assertEqual(check, {"location": "A01", "sku": "A01-1002", "error": "Something is already in A01."})
        check = self.client.get(reverse("resell:location-check"), {"location": "a2"}).json()
        self.assertIsNone(check["error"])
        new_item("B01")
        jump = self.client.get(reverse("resell:location-next-bin"), {"after": "A02"}).json()
        self.assertEqual(jump, {"location": "B02", "sku": "B02-1003"})
        configure(bin_capacity_overrides={"Z": 1})
        new_item("Z01")
        self.assertEqual(self.client.get(reverse("resell:location-next-bin"), {"after": "Y03"}).json()["location"], "AA01")

    def test_moving_a_numbered_item_keeps_its_sku(self):
        item = new_item()
        response = self.post("item-edit", self.form_data(storage_location="B05"), pk=item.pk)
        self.assertNotContains(response, "already in")
        item.refresh_from_db()
        self.assertEqual((item.sku, item.storage_location), ("A01-1001", "B05"))
        self.assertEqual(item.location_history[0]["location"], "A01")

    def test_older_items_keep_free_text_and_editable_sku(self):
        item = self.item(storage_location="Bin 4", sku="OLD-1")
        self.post("item-edit", self.form_data(storage_location="Closet", sku="OLD-2"), pk=item.pk)
        item.refresh_from_db()
        self.assertEqual((item.storage_location, item.sku, item.item_number), ("Closet", "OLD-2", None))


class StatusLocationTests(ResellViewTestCase):
    def sold_item(self):
        item = new_item()
        item.sale_date, item.sale_price, item.platform = TODAY, D("30"), self.ebay
        item.save()
        return item

    def test_ship_frees_the_slot_and_undo_takes_it_back(self):
        item = self.sold_item()
        response = self.post("item-ship", pk=item.pk)
        self.assertContains(response, "A01 is free")
        item.refresh_from_db()
        self.assertIsNotNone(item.shipped_at)
        self.assertEqual(locations.next_open_location(), "A01")
        self.post("item-ship-undo", pk=item.pk)
        item.refresh_from_db()
        self.assertIsNone(item.location_released_at)
        self.assertEqual(item.storage_location, "A01")

    def test_return_gets_a_new_slot_and_keeps_its_sku(self):
        item = self.sold_item()
        self.post("item-ship", pk=item.pk)
        new_item()  # takes A01 while the item is away
        response = self.post("item-return", {"return_date": TODAY.isoformat(), "return_cost": "0"}, pk=item.pk)
        self.assertContains(response, "Shelve it in A02")
        item.refresh_from_db()
        self.assertEqual((item.sku, item.storage_location, item.status), ("A01-1001", "A02", Item.RETURNED))
        self.assertIsNone(item.location_released_at)
        self.assertEqual([old["location"] for old in item.location_history], ["A01"])

    def test_return_before_shipping_keeps_the_slot(self):
        item = self.sold_item()
        self.post("item-return", {"return_date": TODAY.isoformat(), "return_cost": "0"}, pk=item.pk)
        item.refresh_from_db()
        self.assertEqual((item.storage_location, item.location_history), ("A01", []))

    def test_write_off_and_undo(self):
        item = new_item()
        self.post("item-write-off", {"written_off_date": TODAY.isoformat()}, pk=item.pk)
        self.assertEqual(locations.next_open_location(), "A01")
        new_item()  # A01 is reused
        response = self.post("item-write-off-undo", pk=item.pk)
        self.assertContains(response, "Put it in A02")
        item.refresh_from_db()
        self.assertEqual((item.sku, item.storage_location), ("A01-1001", "A02"))

    def test_locations_report_lists_items_to_ship(self):
        item = self.sold_item()
        response = self.client.get(reverse("resell:report-locations"))
        self.assertContains(response, "To ship")
        self.assertContains(response, "A01-1001")
        self.post("item-ship", pk=item.pk)
        response = self.client.get(reverse("resell:report-locations"))
        self.assertContains(response, "Nothing waiting to ship.")
        self.assertNotContains(response, "A01-1001")

    def test_undo_sale_after_shipping_restocks(self):
        item = self.sold_item()
        self.post("item-ship", pk=item.pk)
        self.post("item-sale-undo", pk=item.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, item.storage_location, item.shipped_at, item.location_released_at),
                         (Item.IN_INVENTORY, "A01", None, None))
