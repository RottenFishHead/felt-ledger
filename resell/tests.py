import io
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from .forms import ItemForm, add_photos
from .models import Brand, Category, Expense, Item, ItemImage, Listing, Platform, Source

D = Decimal
BOUGHT = date(2026, 9, 1)
SOLD_ON = date(2026, 10, 1)


def make_item(**kwargs):
    fields = {"title": "Air Max 90", "purchase_date": BOUGHT, "purchase_price": D("13.00")}
    fields.update(kwargs)
    return Item.objects.create(**fields)


class ResellTestCase(TestCase):
    def setUp(self):
        # eBay and Poshmark already exist (the starter sites migration); set the fees these tests rely on.
        self.ebay, _ = Platform.objects.update_or_create(name="eBay", defaults={"fee_percent": D("13.25"), "fee_fixed": D("0.40")})
        self.posh, _ = Platform.objects.update_or_create(name="Poshmark", defaults={"fee_percent": D("20.00"), "fee_fixed": D("0")})

    def sell(self, item, **kwargs):
        fields = {"sale_date": SOLD_ON, "sale_price": D("80.00"), "platform": self.ebay,
                  "shipping_charged": D("10.00"), "shipping_cost": D("9.50")}
        fields.update(kwargs)
        for name, value in fields.items():
            setattr(item, name, value)
        item.save()
        return item


class ProfitTests(ResellTestCase):
    def test_sold_item_profit_and_fees(self):
        item = self.sell(make_item())
        self.assertEqual(item.status, Item.SOLD)
        self.assertEqual(item.platform_fees, D("12.33"))  # 13.25% of $90 + $0.40 = 12.325, rounded half up
        self.assertEqual(item.profit, D("55.17"))  # 90 - 13 - 9.50 - 12.33
        self.assertEqual(item.roi_percent, D("424.4"))
        self.assertEqual(item.margin_percent, D("69.0"))
        self.assertEqual(item.days_to_sell, 30)
        self.assertIsNone(item.days_in_inventory)

    def test_unsold_item_has_no_profit(self):
        item = make_item()
        self.assertIsNone(item.profit)
        self.assertIsNone(item.roi_percent)
        self.assertEqual(item.days_in_inventory, (timezone.localdate() - BOUGHT).days)

    def test_manual_fees_are_kept(self):
        item = self.sell(make_item(), platform_fees=D("5.00"))
        self.assertEqual(item.platform_fees, D("5.00"))
        self.assertEqual(item.profit, D("62.50"))

    def test_categories_many_to_many(self):
        item = make_item(brand=Brand.objects.create(name="Nike"))
        shoes = Category.objects.create(name="Shoes")
        item.subcategories.set([shoes.subcategories.create(name="Sneakers"), shoes.subcategories.create(name="Vintage")])
        self.assertEqual(sorted(item.subcategories.values_list("name", flat=True)), ["Sneakers", "Vintage"])


class RecalculateFeesTests(ResellTestCase):
    def form_data(self, item, **overrides):
        data = {"title": item.title, "condition": item.condition, "purchase_date": item.purchase_date,
                "purchase_price": item.purchase_price,
                "sale_date": item.sale_date, "sale_price": item.sale_price, "platform": item.platform_id,
                "shipping_charged": item.shipping_charged, "shipping_cost": item.shipping_cost,
                "platform_fees": item.platform_fees, "other_sale_costs": item.other_sale_costs,
                "return_cost": item.return_cost}
        data.update(overrides)
        return data

    def test_fees_stay_when_price_changes_without_checkbox(self):
        item = self.sell(make_item())
        form = ItemForm(self.form_data(item, sale_price="70.00"), instance=item)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().platform_fees, D("12.33"))

    def test_checkbox_recalculates_fees(self):
        item = self.sell(make_item())
        form = ItemForm(self.form_data(item, sale_price="70.00", recalculate_fees="on"), instance=item)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().platform_fees, D("11.00"))  # 13.25% of $80 + $0.40


class StatusTests(ResellTestCase):
    def test_new_item_is_in_inventory(self):
        self.assertEqual(make_item().status, Item.IN_INVENTORY)

    def test_active_listing_marks_item_listed(self):
        item = make_item()
        listing = Listing.objects.create(item=item, platform=self.ebay, price=D("85.00"))
        item.refresh_from_db()
        self.assertEqual(item.status, Item.LISTED)
        listing.status = Listing.ENDED
        listing.save()
        item.refresh_from_db()
        self.assertEqual(item.status, Item.IN_INVENTORY)
        self.assertIsNotNone(listing.ended_date)

    def test_deleting_last_listing_returns_item_to_inventory(self):
        item = make_item()
        Listing.objects.create(item=item, platform=self.ebay).delete()
        item.refresh_from_db()
        self.assertEqual(item.status, Item.IN_INVENTORY)

    def test_written_off(self):
        item = make_item(written_off_date=date(2026, 9, 20))
        self.assertEqual(item.status, Item.WRITTEN_OFF)
        self.assertEqual(item.profit, D("-13.00"))

    def test_status_cannot_be_set_by_hand(self):
        item = make_item()
        item.status = Item.SOLD
        item.save()
        self.assertEqual(item.status, Item.IN_INVENTORY)


class CrossListingTests(ResellTestCase):
    def test_item_can_be_listed_on_several_sites(self):
        item = make_item()
        Listing.objects.create(item=item, platform=self.ebay, price=D("85.00"))
        Listing.objects.create(item=item, platform=self.posh, price=D("90.00"))
        self.assertEqual(item.listings.filter(status=Listing.ACTIVE).count(), 2)

    def test_sale_marks_that_sites_listing_sold_and_flags_the_rest(self):
        item = make_item()
        ebay_listing = Listing.objects.create(item=item, platform=self.ebay)
        posh_listing = Listing.objects.create(item=item, platform=self.posh)
        item.refresh_from_db()
        self.sell(item)
        ebay_listing.refresh_from_db()
        self.assertEqual(ebay_listing.status, Listing.SOLD)
        self.assertEqual(ebay_listing.ended_date, SOLD_ON)
        self.assertEqual(list(item.listings_to_end), [posh_listing])
        item.refresh_from_db()
        self.assertEqual(item.status, Item.SOLD)  # still-active Poshmark listing doesn't undo the sale

    def test_listing_added_after_sale_on_same_site_is_marked_sold(self):
        item = self.sell(make_item())
        listing = Listing.objects.create(item=item, platform=self.ebay)
        self.assertEqual(listing.status, Listing.SOLD)

    def test_one_active_listing_per_site(self):
        item = make_item()
        Listing.objects.create(item=item, platform=self.ebay)
        with self.assertRaises(IntegrityError):
            Listing.objects.create(item=item, platform=self.ebay)

    def test_relisting_after_ending_is_allowed(self):
        item = make_item()
        Listing.objects.create(item=item, platform=self.ebay, status=Listing.ENDED)
        Listing.objects.create(item=item, platform=self.ebay)
        self.assertEqual(item.listings.count(), 2)


class ReturnTests(ResellTestCase):
    def test_return_cancels_sale(self):
        item = self.sell(make_item())
        item.return_date = date(2026, 10, 10)
        item.return_cost = D("8.00")
        item.save()
        self.assertEqual(item.status, Item.RETURNED)
        self.assertIsNone(item.profit)
        self.assertIsNotNone(item.days_in_inventory)

    def test_relisted_after_return(self):
        item = self.sell(make_item(), return_date=date(2026, 10, 10))
        Listing.objects.create(item=item, platform=self.posh)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.LISTED)

    def test_resale_after_return_includes_return_cost(self):
        item = self.sell(make_item(), return_date=date(2026, 10, 10), return_cost=D("8.00"))
        self.sell(item, sale_date=date(2026, 10, 20), platform_fees=None)
        self.assertEqual(item.status, Item.SOLD)
        self.assertEqual(item.profit, D("47.17"))  # first-sale profit minus the $8 return cost

    def test_written_off_after_return_includes_return_cost(self):
        item = self.sell(make_item(), return_date=date(2026, 10, 10), return_cost=D("8.00"))
        item.written_off_date = date(2026, 11, 1)
        item.save()
        self.assertEqual(item.profit, D("-21.00"))


class ValidationTests(ResellTestCase):
    def assert_errors(self, item, fields):
        with self.assertRaises(ValidationError) as caught:
            item.full_clean()
        self.assertEqual(sorted(caught.exception.message_dict), sorted(fields))

    def test_negative_money_rejected(self):
        self.assert_errors(Item(title="x", purchase_price=D("-1")), ["purchase_price"])

    def test_sale_needs_date_price_and_platform(self):
        self.assert_errors(Item(title="x", sale_price=D("10")), ["sale_date", "platform"])

    def test_dates_cannot_precede_purchase(self):
        self.assert_errors(Item(title="x", purchase_date=BOUGHT, sale_date=BOUGHT - timedelta(days=1),
                                sale_price=D("10"), platform=self.ebay), ["sale_date"])

    def test_return_cannot_precede_purchase(self):
        self.assert_errors(Item(title="x", purchase_date=SOLD_ON, return_date=BOUGHT), ["return_date"])

    def test_sold_item_cannot_be_written_off(self):
        self.assert_errors(Item(title="x", purchase_date=BOUGHT, sale_date=SOLD_ON, sale_price=D("10"), platform=self.ebay,
                                written_off_date=SOLD_ON), ["written_off_date"])

    def test_listing_end_before_start_rejected(self):
        listing = Listing(item=make_item(), platform=self.ebay, listed_date=SOLD_ON, ended_date=BOUGHT)
        with self.assertRaises(ValidationError):
            listing.full_clean()


class ProtectTests(ResellTestCase):
    def test_platform_with_sales_cannot_be_deleted(self):
        self.sell(make_item())
        with self.assertRaises(ProtectedError):
            self.ebay.delete()

    def test_platform_with_listings_cannot_be_deleted(self):
        Listing.objects.create(item=make_item(), platform=self.posh)
        with self.assertRaises(ProtectedError):
            self.posh.delete()

    def test_unused_platform_can_be_deleted(self):
        self.posh.delete()  # nothing listed or sold there
        self.assertFalse(Platform.objects.filter(name="Poshmark").exists())


class ExpenseTests(TestCase):
    def test_mileage_amount_calculated(self):
        expense = Expense.objects.create(category=Expense.MILEAGE, miles=D("42.5"), mileage_rate=D("0.70"))
        self.assertEqual(expense.amount, D("29.75"))

    def test_mileage_rounds_half_cents_up(self):
        expense = Expense.objects.create(category=Expense.MILEAGE, miles=D("1"), mileage_rate=D("0.725"))
        self.assertEqual(expense.amount, D("0.73"))

    @override_settings(RESELL_MILEAGE_RATE="0.65")
    def test_mileage_rate_defaults_from_settings(self):
        expense = Expense.objects.create(category=Expense.MILEAGE, miles=D("10"))
        self.assertEqual(expense.mileage_rate, D("0.65"))
        self.assertEqual(expense.amount, D("6.50"))

    def test_mileage_requires_miles(self):
        with self.assertRaises(ValidationError):
            Expense(category=Expense.MILEAGE).full_clean()

    def test_regular_expense_requires_amount(self):
        with self.assertRaises(ValidationError):
            Expense(category=Expense.SUPPLIES).full_clean()


class SourceMapTests(TestCase):
    def test_map_urls(self):
        source = Source(name="Goodwill", street_address="123 Main St", city="Springfield", state="IL")
        self.assertIn("query=Goodwill%2C+123+Main+St%2C+Springfield%2C+IL", source.map_url)
        self.assertEqual(Source(name="eBay lots", kind=Source.ONLINE).map_embed_url, "")


def image_upload(name="photo.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class PhotoShrinkTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def upload(self, image, name, fmt, **save_kwargs):
        buffer = io.BytesIO()
        image.save(buffer, fmt, **save_kwargs)
        return SimpleUploadedFile(name, buffer.getvalue())

    def test_big_phone_photo_is_shrunk_turned_upright_and_stripped(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # the camera says: rotate 90° to view
        exif[0x8825] = {2: (40.0, 26.0, 46.0)}  # GPS latitude
        upload = self.upload(Image.new("RGB", (4000, 3000), "red"), "IMG_0001.jpeg", "JPEG", exif=exif)
        photo = ItemImage.objects.create(item=make_item(), image=upload)
        self.assertTrue(photo.image.name.endswith("IMG_0001.jpg"))
        with Image.open(photo.image.path) as saved:
            self.assertEqual((saved.format, saved.size), ("JPEG", (1200, 1600)))  # portrait now, long side 1600
            self.assertNotIn(0x8825, saved.getexif())

    def test_transparent_png_goes_on_white(self):
        photo = ItemImage.objects.create(item=make_item(), image=self.upload(Image.new("RGBA", (10, 10)), "logo.png", "PNG"))
        with Image.open(photo.image.path) as saved:
            self.assertEqual((saved.format, saved.size, saved.getpixel((5, 5))), ("JPEG", (10, 10), (255, 255, 255)))

    def test_ten_photos_at_once(self):
        item = make_item()
        add_photos(item, [self.upload(Image.new("RGB", (2000, 2000)), f"p{n}.jpg", "JPEG") for n in range(10)])
        self.assertEqual(item.images.count(), 10)


class FileCleanupTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def stored(self, field_file):
        return field_file.storage.exists(field_file.name)

    def test_deleting_image_deletes_file(self):
        image = ItemImage.objects.create(item=make_item(), image=image_upload())
        self.assertTrue(self.stored(image.image))
        image.delete()
        self.assertFalse(self.stored(image.image))

    def test_deleting_item_deletes_all_its_image_files(self):
        item = make_item()
        images = [ItemImage.objects.create(item=item, image=image_upload(), sort_order=n) for n in range(3)]
        item.delete()
        for image in images:
            self.assertFalse(self.stored(image.image))

    def test_replacing_image_deletes_old_file(self):
        image = ItemImage.objects.create(item=make_item(), image=image_upload("old.jpg"))
        old = image.image.name
        image.image = image_upload("new.jpg")
        image.save()
        self.assertFalse(image.image.storage.exists(old))
        self.assertTrue(self.stored(image.image))

    def test_expense_receipt_deleted_with_expense(self):
        expense = Expense.objects.create(amount=D("15.00"), receipt=image_upload("receipt.jpg"))
        self.assertTrue(expense.receipt_is_image)
        self.assertTrue(self.stored(expense.receipt))
        expense.delete()
        self.assertFalse(self.stored(expense.receipt))

    def test_pdf_receipt_allowed_but_not_other_types(self):
        Expense(amount=D("5"), receipt=SimpleUploadedFile("r.pdf", b"%PDF-1.4")).full_clean()
        with self.assertRaises(ValidationError):
            Expense(amount=D("5"), receipt=SimpleUploadedFile("r.exe", b"MZ")).full_clean()


class MediaAccessTests(FileCleanupTests):
    def test_uploads_require_sign_in(self):
        expense = Expense.objects.create(amount=D("15.00"), receipt=image_upload("receipt.jpg"))
        url = expense.receipt.url
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        user = get_user_model().objects.create_user(username="gail", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 403)  # signed in, but not in the Resell group
        user.groups.add(Group.objects.get(name="Resell"))
        self.assertEqual(self.client.get(url).status_code, 200)
