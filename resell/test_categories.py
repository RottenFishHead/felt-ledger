from datetime import timedelta
from django.db import IntegrityError, transaction
from django.urls import reverse
from . import reports
from .models import Category, Item, SubCategory
from .test_views import TODAY, ResellViewTestCase


class ItemTypeTests(ResellViewTestCase):
    def setUp(self):
        super().setUp()
        self.shoes = Category.objects.create(name="Shoes")
        self.sneakers = self.shoes.subcategories.create(name="Sneakers")
        self.boots = self.shoes.subcategories.create(name="Boots")
        self.bags = Category.objects.create(name="Bags")
        self.totes = self.bags.subcategories.create(name="Totes")

    def types(self, item):
        return sorted(str(sub) for sub in item.subcategories.all())

    def new_item(self, **data):
        fields = {"title": "Kicks", "condition": "good", "purchase_date": TODAY.isoformat(), "purchase_price": "5"}
        return self.post("item-create", {**fields, **data})

    def test_full_name(self):
        self.assertEqual(str(self.sneakers), "Shoes › Sneakers")

    def test_names_are_unique_ignoring_capitals(self):
        self.bags.subcategories.create(name="Sneakers")  # same name in a different main type is fine
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.shoes.subcategories.create(name="sneakers")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name="shoes")

    def test_items_only_link_to_sub_types(self):
        item = self.item()
        item.subcategories.add(self.sneakers, self.totes)
        item.subcategories.add(self.sneakers)  # the same sub-type twice is stored once
        self.assertEqual(self.types(item), ["Bags › Totes", "Shoes › Sneakers"])
        with self.assertRaises(TypeError):
            item.subcategories.add(self.shoes)  # a main type is a different model — the database won't take it

    def test_picker_offers_only_sub_types(self):
        page = self.client.get(reverse("resell:item-create")).content.decode()
        self.assertIn('data-full="Shoes › Sneakers"', page)
        self.assertIn('<summary class="category-main"><span>Shoes', page)

    def test_item_form_ticks_and_adds_sub_types(self):
        self.new_item(subcategories=[self.sneakers.pk], new_subcategories="Heels, Flats", new_subcategory_category="shoes")
        self.assertEqual(self.types(Item.objects.get()), ["Shoes › Flats", "Shoes › Heels", "Shoes › Sneakers"])
        self.assertEqual(Category.objects.count(), 2)  # "shoes" matched the existing Shoes

    def test_new_main_type_typed_on_the_item(self):
        self.new_item(new_subcategories="Lamps", new_subcategory_category="Home decor")
        self.assertEqual(self.types(Item.objects.get()), ["Home decor › Lamps"])

    def test_new_sub_type_needs_a_main_type_and_vice_versa(self):
        self.assertContains(self.new_item(new_subcategories="Lamps"), "Every sub-type goes under a main type")
        self.assertContains(self.new_item(new_subcategory_category="Shoes"), "Type the new sub-type")
        self.assertFalse(Item.objects.exists())

    def test_editing_opens_the_main_type_of_a_ticked_sub_type(self):
        self.new_item(subcategories=[self.sneakers.pk])
        page = self.client.get(reverse("resell:item-edit", kwargs={"pk": Item.objects.get().pk})).content.decode()
        self.assertIn("Shoes › Sneakers</span>", page)
        self.assertRegex(page, r'<details class="category-group" data-category-group open>\s*<summary class="category-main"><span>Shoes')

    def test_type_pages(self):
        self.item().subcategories.add(self.sneakers)
        page = self.client.get(reverse("resell:category-list"))
        self.assertContains(page, "2 sub-types · 1 item")
        self.assertContains(page, reverse("resell:subcategory-create", kwargs={"category_pk": self.shoes.pk}))
        self.post("category-create", {"name": "Jackets"})
        self.assertTrue(Category.objects.filter(name="Jackets").exists())
        self.post("subcategory-create", {"category": self.bags.pk, "name": "Crossbody"}, category_pk=self.bags.pk)
        self.assertTrue(self.bags.subcategories.filter(name="Crossbody").exists())
        self.post("subcategory-edit", {"category": self.shoes.pk, "name": "Totes"}, pk=self.totes.pk)  # move it
        self.totes.refresh_from_db()
        self.assertEqual(self.totes.category, self.shoes)
        response = self.post("subcategory-edit", {"category": self.shoes.pk, "name": "sneakers"}, pk=self.totes.pk)
        self.assertContains(response, "already has a sub-type with this name")
        self.assertContains(self.post("category-delete", pk=self.shoes.pk), "still has sub-types")
        self.assertTrue(Category.objects.filter(pk=self.shoes.pk).exists())
        self.post("subcategory-delete", pk=self.sneakers.pk)
        self.assertFalse(SubCategory.objects.filter(pk=self.sneakers.pk).exists())

    def test_report_rolls_sub_types_into_main_type(self):
        for sub in (self.sneakers, self.boots):
            item = self.item()
            item.subcategories.add(sub)
            item.sale_date, item.sale_price, item.platform = TODAY, 20, self.ebay
            item.save()
        report = reports.sales_by_category(TODAY - timedelta(days=1), TODAY)
        self.assertEqual([(row.cells[0], row.cells[1]) for row in report.tables[0].rows], [("Shoes", 2)])
        self.assertEqual(sorted(row.cells[0] for row in report.tables[1].rows), ["Shoes › Boots", "Shoes › Sneakers"])
