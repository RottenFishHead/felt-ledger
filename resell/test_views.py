import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Expense, Item, ItemImage, Listing, Platform, Source
from .tests import image_upload

TODAY = timezone.localdate()


class ResellViewTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.user = get_user_model().objects.create_user(username="jo", password="pw")
        self.user.groups.add(Group.objects.get(name="Resell"))
        self.client.force_login(self.user)
        self.ebay = Platform.objects.get(name="eBay")
        self.ebay.fee_percent, self.ebay.fee_fixed = D("13.25"), D("0.40")
        self.ebay.save()
        self.posh = Platform.objects.get(name="Poshmark")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def item(self, **kwargs):
        fields = {"title": "Coach purse", "purchase_date": TODAY - timedelta(days=10), "purchase_price": D("10.00")}
        fields.update(kwargs)
        return Item.objects.create(**fields)

    def post(self, name, data=None, follow=True, **kwargs):
        return self.client.post(reverse(f"resell:{name}", kwargs=kwargs), data or {}, follow=follow)


class AccessTests(ResellViewTestCase):
    def test_signed_out_goes_to_sign_in(self):
        self.client.logout()
        response = self.client.get(reverse("resell:home"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('resell:home')}")

    def test_user_outside_group_is_refused(self):
        outsider = get_user_model().objects.create_user(username="stranger", password="pw")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("resell:home")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Go to Resell")

    def test_superuser_allowed_and_sees_switch(self):
        admin = get_user_model().objects.create_superuser(username="boss", password="pw")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("resell:home")).status_code, 200)
        self.assertContains(self.client.get(reverse("dashboard")), "Go to Resell")

    def test_every_page_loads(self):
        item = self.item()
        listing = Listing.objects.create(item=item, platform=self.ebay)
        expense = Expense.objects.create(amount=D("5"))
        source = Source.objects.create(name="Goodwill")
        names = [("home", {}), ("help", {}), ("item-list", {}), ("item-create", {}), ("item-detail", {"pk": item.pk}),
                 ("item-edit", {"pk": item.pk}), ("item-delete", {"pk": item.pk}), ("item-sell", {"pk": item.pk}),
                 ("item-write-off", {"pk": item.pk}), ("item-return-fix", {"pk": item.pk}),
                 ("listing-create", {"item_pk": item.pk}), ("listing-edit", {"pk": listing.pk}),
                 ("listing-delete", {"pk": listing.pk}), ("expense-list", {}), ("expense-create", {}),
                 ("expense-edit", {"pk": expense.pk}), ("expense-delete", {"pk": expense.pk}),
                 ("source-list", {}), ("source-create", {}), ("source-edit", {"pk": source.pk}),
                 ("platform-list", {}), ("platform-create", {}), ("platform-edit", {"pk": self.ebay.pk})]
        for name, kwargs in names:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"resell:{name}", kwargs=kwargs)).status_code, 200)

    def test_resell_pages_use_navy_theme_and_own_menu(self):
        response = self.client.get(reverse("resell:home"))
        self.assertContains(response, 'data-app="resell"')
        self.assertContains(response, "Go to Poker")
        self.assertContains(response, "My items")


class ItemFlowTests(ResellViewTestCase):
    def test_add_item_with_new_brand_type_place_and_photos(self):
        response = self.post("item-create", {
            "title": "Nike Air Max 90", "brand_name": "Nike", "new_subcategories": "Sneakers, Vintage", "new_subcategory_category": "Shoes",
            "condition": "good", "purchase_date": TODAY.isoformat(), "purchase_price": "12.00", "new_source_name": "Goodwill Main St",
            "photos": [image_upload("a.jpg"), image_upload("b.jpg")],
        })
        item = Item.objects.get()
        self.assertRedirects(response, item.get_absolute_url())
        self.assertContains(response, "Saved “Nike Air Max 90”")
        self.assertEqual(item.brand.name, "Nike")
        self.assertEqual(sorted(item.subcategories.values_list("name", flat=True)), ["Sneakers", "Vintage"])
        self.assertEqual(item.source.name, "Goodwill Main St")
        self.assertEqual(item.images.count(), 2)

    def test_existing_brand_reused_ignoring_capitals(self):
        self.post("item-create", {"title": "A", "brand_name": "Nike", "condition": "good",
                                  "purchase_date": TODAY.isoformat(), "purchase_price": "1"})
        self.post("item-create", {"title": "B", "brand_name": "nike", "condition": "good",
                                  "purchase_date": TODAY.isoformat(), "purchase_price": "1"})
        self.assertEqual(Item.objects.filter(brand__name="Nike").count(), 2)

    def test_missing_title_shows_friendly_error(self):
        response = self.post("item-create", {"condition": "good", "purchase_date": TODAY.isoformat(),
                                             "purchase_price": "1"})
        self.assertContains(response, "Almost there!")
        self.assertFalse(Item.objects.exists())

    def test_picking_and_typing_a_place_is_an_error(self):
        source = Source.objects.create(name="Goodwill")
        response = self.post("item-create", {"title": "A", "condition": "good", "purchase_date": TODAY.isoformat(),
                                             "purchase_price": "1",
                                             "source": source.pk, "new_source_name": "Other"})
        self.assertContains(response, "not both")

    def test_full_life_of_an_item(self):
        item = self.item()
        # Listed on two sites
        self.post("listing-create", {"platform": self.ebay.pk, "price": "60", "listed_date": TODAY.isoformat()}, item_pk=item.pk)
        self.post("listing-create", {"platform": self.posh.pk, "price": "65", "listed_date": TODAY.isoformat()}, item_pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.LISTED)
        # Can't list twice on the same site
        response = self.post("listing-create", {"platform": self.ebay.pk, "price": "1", "listed_date": TODAY.isoformat()}, item_pk=item.pk)
        self.assertContains(response, "already listed on eBay")

        # Sold on eBay, fees left blank so they're estimated
        response = self.post("item-sell", {"sale_date": TODAY.isoformat(), "platform": self.ebay.pk, "sale_price": "50",
                                           "shipping_charged": "0", "shipping_cost": "5", "platform_fees": "",
                                           "other_sale_costs": "0"}, pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.SOLD)
        self.assertEqual(item.platform_fees, D("7.03"))
        self.assertContains(response, "you made $27.97")
        self.assertContains(response, "still listed on Poshmark")

        # Home page reminds to take Poshmark down; "Done" clears it
        home = self.client.get(reverse("resell:home"))
        self.assertContains(home, "Needs your attention")
        posh_listing = item.listings.get(platform=self.posh)
        response = self.post("listing-end", {"next": reverse("resell:home")}, pk=posh_listing.pk)
        self.assertRedirects(response, reverse("resell:home"))
        self.assertNotContains(response, "Needs your attention")

        # Returned, then resold
        self.post("item-return", {"return_date": TODAY.isoformat(), "return_cost": "4.00", "return_reason": "Too small"}, pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.RETURNED)
        response = self.post("item-sell", {"sale_date": TODAY.isoformat(), "platform": self.posh.pk, "sale_price": "40",
                                           "shipping_charged": "0", "shipping_cost": "0", "platform_fees": "8",
                                           "other_sale_costs": "0"}, pk=item.pk)
        self.assertContains(response, "must be after that")  # same day as the return
        tomorrow = (TODAY + timedelta(days=1)).isoformat()
        self.post("item-sell", {"sale_date": tomorrow, "platform": self.posh.pk, "sale_price": "40",
                                "shipping_charged": "0", "shipping_cost": "0", "platform_fees": "8",
                                "other_sale_costs": "0"}, pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.SOLD)
        self.assertEqual(item.profit, D("18.00"))  # 40 - 10 - 8 - 4 (return)

    def test_sell_prefills_single_listing(self):
        item = self.item()
        Listing.objects.create(item=item, platform=self.posh, price=D("33.00"))
        form = self.client.get(reverse("resell:item-sell", kwargs={"pk": item.pk})).context["form"]
        self.assertEqual(form.initial["platform"], self.posh.pk)
        self.assertEqual(form.initial["sale_price"], D("33.00"))

    def test_undo_sale_reactivates_listing(self):
        item = self.item()
        listing = Listing.objects.create(item=item, platform=self.ebay)
        self.post("item-sell", {"sale_date": TODAY.isoformat(), "platform": self.ebay.pk, "sale_price": "50",
                                "shipping_charged": "0", "shipping_cost": "0", "platform_fees": "5",
                                "other_sale_costs": "0"}, pk=item.pk)
        self.post("item-sale-undo", pk=item.pk)
        item.refresh_from_db()
        listing.refresh_from_db()
        self.assertIsNone(item.sale_price)
        self.assertEqual(listing.status, Listing.ACTIVE)
        self.assertEqual(item.status, Item.LISTED)

    def test_write_off_and_undo(self):
        item = self.item()
        self.post("item-write-off", {"written_off_date": TODAY.isoformat(), "reason": "Donated"}, pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.WRITTEN_OFF)
        self.assertIn("Donated", item.notes)
        response = self.client.get(reverse("resell:item-sell", kwargs={"pk": item.pk}), follow=True)
        self.assertContains(response, "Undo write-off")
        self.post("item-write-off-undo", pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.IN_INVENTORY)

    def test_fix_return_can_remove_it(self):
        item = self.item(sale_date=TODAY, sale_price=D("20"), platform=self.ebay, return_date=TODAY, return_cost=D("3"))
        self.post("item-return-fix", {"return_date": TODAY.isoformat(), "return_cost": "3", "return_reason": "",
                                      "remove_return": "on"}, pk=item.pk)
        item.refresh_from_db()
        self.assertIsNone(item.return_date)
        self.assertEqual(item.status, Item.SOLD)

    def test_delete_item_needs_confirmation(self):
        item = self.item()
        self.assertContains(self.client.get(reverse("resell:item-delete", kwargs={"pk": item.pk})), "This can't be undone")
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.post("item-delete", pk=item.pk)
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_pick_mode_links_straight_to_forms(self):
        item = self.item()
        self.item(title="Sold thing", sale_date=TODAY, sale_price=D("5"), platform=self.ebay)
        response = self.client.get(reverse("resell:item-list"), {"action": "sell"})
        self.assertContains(response, reverse("resell:item-sell", kwargs={"pk": item.pk}))
        self.assertNotContains(response, "Sold thing")  # already sold, so not offered

    def test_search_and_tabs(self):
        self.item(title="Red dress", storage_location="Bin 4")
        self.item(title="Blue jeans")
        response = self.client.get(reverse("resell:item-list"), {"q": "bin 4"})
        self.assertContains(response, "Red dress")
        self.assertNotContains(response, "Blue jeans")
        self.assertContains(self.client.get(reverse("resell:item-list"), {"show": "sold"}), "No items here yet")


class PhotoTests(ResellViewTestCase):
    def test_add_make_main_and_delete(self):
        item = self.item()
        self.post("photo-add", {"photos": [image_upload("a.jpg"), image_upload("b.jpg")]}, item_pk=item.pk)
        first, second = item.images.all()
        self.post("photo-cover", pk=second.pk)
        self.assertEqual(item.images.first(), second)
        self.post("photo-delete", pk=first.pk)
        self.assertEqual(list(item.images.all()), [second])

    def test_non_image_rejected(self):
        item = self.item()
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.post("photo-add", {"photos": [SimpleUploadedFile("x.txt", b"hello")]}, item_pk=item.pk)
        self.assertFalse(ItemImage.objects.exists())
        self.assertContains(response, "image")


class ExpenseAndSetupTests(ResellViewTestCase):
    def test_mileage_expense(self):
        self.post("expense-create", {"category": "mileage", "date": TODAY.isoformat(), "miles": "20",
                                     "mileage_rate": "0.70", "amount": "", "description": "Estate sale run"})
        expense = Expense.objects.get()
        self.assertEqual(expense.amount, D("14.00"))

    def test_regular_expense_with_receipt(self):
        response = self.post("expense-create", {"category": "supplies", "date": TODAY.isoformat(), "amount": "18.50",
                                                "mileage_rate": "0.70", "miles": "999", "receipt": image_upload("r.jpg")})
        expense = Expense.objects.get()
        self.assertIsNone(expense.miles)  # miles ignored for non-mileage expenses
        self.assertTrue(expense.receipt)
        self.assertContains(response, "$18.50")

    def test_expense_needs_amount(self):
        response = self.post("expense-create", {"category": "supplies", "date": TODAY.isoformat(), "amount": ""})
        self.assertContains(response, "Enter the expense amount")

    def test_home_profit_after_expenses(self):
        self.item(sale_date=TODAY, sale_price=D("50"), platform=self.ebay, platform_fees=D("5"))
        Expense.objects.create(amount=D("15"), date=TODAY)
        response = self.client.get(reverse("resell:home"))
        self.assertEqual(response.context["month"]["profit"], D("35.00"))
        self.assertEqual(response.context["month"]["net"], D("20.00"))

    def test_used_platform_cannot_be_deleted(self):
        self.item(sale_date=TODAY, sale_price=D("50"), platform=self.ebay)
        response = self.post("platform-delete", pk=self.ebay.pk)
        self.assertContains(response, "be deleted because items were listed or sold there")
        self.assertTrue(Platform.objects.filter(pk=self.ebay.pk).exists())

    def test_add_place_with_address(self):
        self.post("source-create", {"name": "Thrift Town", "kind": "thrift", "street_address": "1 Main St",
                                    "city": "Springfield", "state": "IL", "postal_code": "62701"})
        response = self.client.get(reverse("resell:source-list"))
        self.assertContains(response, "Map & directions")

    def test_starter_sites_exist(self):
        names = set(Platform.objects.values_list("name", flat=True))
        self.assertTrue({"eBay", "Poshmark", "Mercari", "In person"} <= names)
