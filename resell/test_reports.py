from datetime import date, timedelta
from decimal import Decimal as D
from django.urls import reverse
from django.utils.html import escape
from . import reports
from .models import Brand, Category, Expense, Listing, Source
from .report_views import preset_range
from .reports import CATALOG
from .test_views import TODAY, ResellViewTestCase

YEAR_START = date(TODAY.year, 1, 1)


class ReportTestCase(ResellViewTestCase):
    def setUp(self):
        super().setUp()
        self.goodwill = Source.objects.create(name="Goodwill", kind=Source.THRIFT)
        shoes = Category.objects.create(name="Shoes").subcategories.create(name="Sneakers")
        nike = Brand.objects.create(name="Nike")
        # Bought last year, still in stock on Jan 1, sold this year
        self.old = self.item(title="Old boots", purchase_date=YEAR_START - timedelta(days=30), purchase_price=D("20.00"))
        # Bought and sold this year: 50 + 5 shipping − 10 cost − 7 fees − 6 label = 32 profit
        self.flip = self.item(title="Air Max", purchase_date=YEAR_START, purchase_price=D("10.00"), source=self.goodwill,
                              brand=nike, storage_location="Bin 1", sku="A1")
        self.flip.subcategories.add(shoes)
        Listing.objects.create(item=self.flip, platform=self.ebay, listed_date=YEAR_START)
        for item, price, fees in [(self.flip, D("50.00"), D("7.00")), (self.old, D("15.00"), D("2.00"))]:
            item.sale_date, item.sale_price, item.platform, item.platform_fees = YEAR_START, price, self.ebay, fees
            item.shipping_charged, item.shipping_cost = (D("5.00"), D("6.00")) if item is self.flip else (D("0"), D("0"))
            item.save()
        # Bought this year, unsold, never listed
        self.waiting = self.item(title="Lamp", purchase_date=YEAR_START, purchase_price=D("8.00"))
        Expense.objects.create(date=YEAR_START, category=Expense.POSTAGE, amount=D("4.00"))
        Expense.objects.create(date=YEAR_START, category=Expense.MILEAGE, miles=D("10"), mileage_rate=D("0.70"),
                               description="Goodwill run")


class EveryReportTests(ReportTestCase):
    def test_every_report_and_pdf_loads(self):
        for _, entries in CATALOG:
            for name, title, _ in entries:
                with self.subTest(report=name):
                    url = reverse(f"resell:{name}")
                    page = self.client.get(url)
                    self.assertContains(page, escape(title))
                    pdf = self.client.get(url + "?format=pdf")
                    self.assertEqual(pdf["Content-Type"], "application/pdf")
                    self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_index_and_menu_link_every_report(self):
        html = self.client.get(reverse("resell:report-index")).content.decode()
        for _, entries in CATALOG:
            for name, _, _ in entries:
                self.assertGreaterEqual(html.count(f'href="{reverse(f"resell:{name}")}"'), 2)  # the page and the menu

    def test_filters_are_accepted(self):
        url = reverse("resell:report-sales-summary")
        for params in ["?period=last_month", "?by=week", "?start=2020-01-01&end=2020-12-31", "?start=bad&end=x"]:
            self.assertEqual(self.client.get(url + params).status_code, 200)
        self.assertEqual(self.client.get(reverse("resell:report-estimated-tax") + "?rate=abc").status_code, 200)
        self.assertEqual(self.client.get(reverse("resell:report-schedule-c") + "?year=1999").status_code, 200)

    def test_reports_need_resell_access(self):
        self.user.groups.clear()
        self.assertEqual(self.client.get(reverse("resell:report-profit-loss")).status_code, 403)


class NumberTests(ReportTestCase):
    def totals(self, table):
        return table.rows[-1].cells

    def test_profit_loss_net(self):
        report = reports.profit_loss(YEAR_START, TODAY, "year")
        lines = {row.cells[0]: row.cells[1] for row in report.tables[0].rows if row.style != "head"}
        self.assertEqual(lines["Total income"], D("70.00"))
        self.assertEqual(lines["Total cost of goods sold"], D("30.00"))
        self.assertEqual(lines["Total selling costs"], D("15.00"))
        self.assertEqual(lines["Total operating expenses"], D("11.00"))
        self.assertEqual(lines["Net profit"], D("14.00"))

    def test_cogs_and_schedule_c_agree(self):
        values = reports.inventory_values(TODAY.year)
        self.assertEqual((values["beginning"], values["purchases"], values["ending"]), (D("20.00"), D("18.00"), D("8.00")))
        self.assertEqual(values["cogs"], D("30.00"))
        report = reports.schedule_c(TODAY.year)
        lines = {row.cells[0]: row.cells[2] for row in report.tables[0].rows if row.cells[0]}
        self.assertEqual(lines["1"], D("70.00"))
        self.assertEqual(lines["10"], D("9.00"))
        self.assertEqual(lines["27a"], D("10.00"))  # postage + labels
        self.assertEqual(lines["9"], D("7.00"))
        self.assertEqual(lines["31"], D("14.00"))

    def test_sold_items_total(self):
        report = reports.sold_items(YEAR_START, TODAY)
        self.assertEqual(self.totals(report.tables[0])[8], D("25.00"))  # 32 − 7

    def test_unlisted_and_aged(self):
        report = reports.unlisted_backlog()
        self.assertEqual(report.stats[0].value, 1)
        self.assertEqual(report.stats[1].value, D("8.00"))

    def test_sales_tax_splits_direct_sales(self):
        in_person = self.posh.__class__.objects.get(name="In person")
        self.waiting.sale_date, self.waiting.sale_price, self.waiting.platform = TODAY, D("12.00"), in_person
        self.waiting.save()
        report = reports.sales_tax(YEAR_START, TODAY)
        self.assertEqual(report.stats[0].value, D("70.00"))
        self.assertEqual(report.stats[1].value, D("12.00"))

    def test_estimated_tax_rates(self):
        report = reports.estimated_tax(TODAY.year, D("12"))
        self.assertEqual(report.stats[0].value, D("14.00"))
        self.assertGreaterEqual(report.stats[2].value, D("0"))


class FilterTests(ReportTestCase):
    def test_last_12_months_is_this_month_and_the_11_before(self):
        for today, first in [(date(2026, 10, 26), date(2025, 11, 1)), (date(2026, 12, 31), date(2026, 1, 1)),
                             (date(2024, 3, 31), date(2023, 4, 1)), (date(2026, 1, 15), date(2025, 2, 1))]:
            start, end = preset_range("last_12", today)
            self.assertEqual((start, end), (first, today))
            self.assertEqual(len(reports.buckets(start, end, "month")), 12)

    def test_typed_in_nonsense_is_ignored_not_an_error(self):
        for url in ["/resell/reports/sales-summary/?start=2026-13-45&end=2026-01-01",
                    "/resell/expenses/?year=99999",
                    "/resell/items/locations/check/?location=A01&item=abc",
                    "/resell/items/locations/next-bin/?after=A01&item=abc"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
