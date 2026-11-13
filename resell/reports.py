"""Report calculations. Each builder returns a Report — headline stats plus tables — that one template draws on
screen and another turns into a PDF. Views (report_views.py) read the filters from the request and call these."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Min
from django.utils import timezone
from .models import CENT, ZERO, Expense, Item, Listing, Platform
from .templatetags.resell_extras import money, nice_date


# The Reports menu, the reports index and every report's eyebrow: (group, [(url name, title, description)])
CATALOG = [
    ("Sales & performance", [
        ("report-sales-summary", "Sales summary", "Gross sales, items sold, average sale price and profit by day, week or month."),
        ("report-sales-by-platform", "Sales by site", "Sales, fees, profit and margin for each site you sell on."),
        ("report-sales-by-category", "Sales by category & brand", "Which categories and brands earn the most."),
        ("report-sold-items", "Profit per item", "Every sale with its cost, fees, shipping, profit, ROI and margin."),
        ("report-sourcing", "Sourcing performance", "Profit and ROI by type of place and by each place you buy."),
        ("report-sell-through", "Sell-through rate", "How many of the items you listed have sold, by month, site and category."),
        ("report-days-to-sell", "Days to sell", "Average time from buying, and from listing, to the sale."),
        ("report-returns", "Returns", "How many came back, the money lost and why."),
        ("report-top-bottom", "Top & bottom performers", "Best flips, money-losers and categories to avoid."),
    ]),
    ("Inventory", [
        ("report-inventory", "Inventory on hand", "How many unsold items you have and what they cost you."),
        ("report-aged-inventory", "Aged inventory", "Items you've held 90, 180 or 365+ days — time to mark down or relist."),
        ("report-unlisted", "Unlisted backlog", "Items bought but not listed yet, and the money tied up in them."),
        ("report-locations", "Item locations", "Where each unsold item is stored, and sold items still waiting to ship."),
        ("report-cogs", "Year-end inventory & COGS", "Beginning inventory + purchases − ending inventory (Schedule C, Part III)."),
    ]),
    ("Purchases & expenses", [
        ("report-purchases", "Purchases log", "Every buying trip: date, place, items and cost."),
        ("report-expenses", "Expenses by category", "Expenses grouped and matched to Schedule C lines."),
        ("report-platform-fees", "Site fees", "Fees by site, and as a share of sales."),
        ("report-shipping", "Shipping", "Shipping buyers paid vs. what labels cost you."),
        ("report-mileage", "Mileage log", "Business trips: date, purpose, miles and deduction."),
    ]),
    ("Accounting & tax", [
        ("report-profit-loss", "Profit & loss", "Income, cost of goods, selling costs and expenses by month, quarter or year."),
        ("report-cash-flow", "Cash flow", "Money in from sales vs. money out for buying and expenses."),
        ("report-sales-tax", "Sales tax", "Marketplace-collected sales vs. direct sales you may owe tax on."),
        ("report-estimated-tax", "Quarterly estimated tax", "Profit so far with estimated income and self-employment tax."),
        ("report-schedule-c", "Schedule C summary", "Year-end tax package: receipts, COGS, expenses by line and mileage."),
    ]),
]
REPORTS = {name: {"group": group, "title": title, "description": text}
           for group, entries in CATALOG for name, title, text in entries}

# Schedule C (Form 1040) line for each expense category
SCHEDULE_C = {
    Expense.ADVERTISING: ("8", "Advertising"),
    Expense.MILEAGE: ("9", "Car and truck expenses"),
    Expense.PAYMENT_PROCESSING: ("10", "Commissions and fees"),
    Expense.EQUIPMENT: ("13", "Depreciation and section 179"),
    Expense.STORAGE: ("20b", "Rent or lease: other business property"),
    Expense.SUPPLIES: ("22", "Supplies"),
    Expense.SHIPPING_SUPPLIES: ("22", "Supplies"),
    Expense.PHONE_INTERNET: ("25", "Utilities"),
    Expense.POSTAGE: ("27a", "Other expenses"),
    Expense.SUBSCRIPTIONS: ("27a", "Other expenses"),
    Expense.OTHER: ("27a", "Other expenses"),
    Expense.HOME_OFFICE: ("30", "Business use of home"),
}
LINE_ORDER = ["8", "9", "10", "13", "20b", "22", "25", "27a", "30"]

SE_TAX_RATE = Decimal("0.153")
SE_EARNINGS_SHARE = Decimal("0.9235")


# ---------------------------------------------------------------- building blocks

@dataclass
class Link:
    text: str
    url: str

    def __str__(self):
        return self.text


@dataclass
class Stat:
    label: str
    value: object
    kind: str = "money"
    foot: str = ""


@dataclass
class Row:
    cells: list
    style: str = ""  # "", "head" (section label), "sub" (subtotal) or "total"


@dataclass
class Table:
    """columns: [(label, kind)]. Kinds: text, money, signed, pct, int, days, date, miles.
    A "statement" stays a table on phones (it scrolls sideways inside its box) instead of turning into cards."""
    title: str
    columns: list
    rows: list = field(default_factory=list)
    note: str = ""
    empty: str = "Nothing to show for these dates."
    statement: bool = False

    def add(self, *cells, style=""):
        self.rows.append(Row(list(cells), style))

    @property
    def header(self):
        """[(label, right-aligned?)]"""
        return [(label, kind not in ("text", "date")) for label, kind in self.columns]

    @property
    def body(self):
        """[(style, [(value, column label, kind, right-aligned?)])] — what the templates loop over."""
        return [(row.style, [(value, label, kind, kind not in ("text", "date"))
                             for value, (label, kind) in zip(row.cells, self.columns)]) for row in self.rows]


@dataclass
class Report:
    name: str
    stats: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    period: str = ""

    @property
    def title(self):
        return REPORTS[self.name]["title"]

    @property
    def group(self):
        return REPORTS[self.name]["group"]

    @property
    def description(self):
        return REPORTS[self.name]["description"]

    @property
    def landscape(self):
        return any(len(table.columns) > 6 for table in self.tables)


def money_sum(values):
    return sum((value or ZERO for value in values), ZERO)


def pct(part, whole):
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1")) if whole else None


def average(total, count):
    return (Decimal(total) / count).quantize(CENT) if count else None


def average_days(values):
    values = [v for v in values if v is not None]
    return (Decimal(sum(values)) / len(values)).quantize(Decimal("0.1")) if values else None


def item_link(item):
    return Link(item.title, item.get_absolute_url())


def gross(item):
    """What the buyer paid: sale price plus shipping they were charged."""
    return item.sale_price + item.shipping_charged


def fees(item):
    return item.platform_fees or ZERO


def realized_cost(item):
    return item.purchase_price + item.return_cost


def period_text(start, end):
    return f"{nice_date(start)} – {nice_date(end)}"


# Period buckets for the summary, P&L and cash-flow tables
def bucket_start(day, group):
    if group == "day":
        return day
    if group == "week":
        return day - timedelta(days=day.weekday())
    if group == "month":
        return day.replace(day=1)
    if group == "quarter":
        return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    return date(day.year, 1, 1)


def bucket_label(start, group):
    if group == "day":
        return f"{start:%a}, {nice_date(start)}"
    if group == "week":
        return f"Week of {nice_date(start)}"
    if group == "month":
        return f"{start:%B %Y}"
    if group == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    return str(start.year)


def buckets(start, end, group):
    """Every bucket that overlaps the dates, in order."""
    keys, day = [], bucket_start(start, group)
    while day <= end:
        keys.append(day)
        if group == "day":
            day += timedelta(days=1)
        elif group == "week":
            day += timedelta(days=7)
        else:
            months = {"month": 1, "quarter": 3}.get(group, 12)
            month = day.month - 1 + months
            day = date(day.year + month // 12, month % 12 + 1, 1)
    return keys


# ---------------------------------------------------------------- queries

def items():
    return Item.objects.select_related("platform", "brand", "source").prefetch_related("subcategories__category")


def sold_between(start, end):
    return list(items().filter(status=Item.SOLD, sale_date__range=(start, end)).order_by("sale_date", "pk"))


def written_off_between(start, end):
    return list(items().filter(status=Item.WRITTEN_OFF, written_off_date__range=(start, end)))


def unsold_items():
    return list(items().filter(status__in=Item.UNSOLD).order_by("purchase_date", "pk"))


def expenses_between(start, end):
    return list(Expense.objects.filter(date__range=(start, end)).order_by("date", "pk"))


def category_names(item):
    """The item's sub-types, e.g. ["Shoes › Sneakers"]."""
    return [str(sub) for sub in item.subcategories.all()] or ["No category"]


def main_category_names(item):
    """Each main type the item belongs to, counted once even if it has several of its sub-types."""
    return sorted({sub.category.name for sub in item.subcategories.all()}) or ["No category"]


def in_stock_on(item, day):
    """Was the item bought and still unsold (and not written off) at the end of this day?"""
    if item.purchase_date > day:
        return False
    if item.written_off_date and item.written_off_date <= day:
        return False
    if item.sale_date and item.sale_price is not None and item.sale_date <= day:
        return bool(item.return_date and item.sale_date <= item.return_date <= day)
    return True


class Totals:
    """Running sums for one group of sold items (a site, a category, a place…)."""

    def __init__(self):
        self.count = 0
        self.sales = self.fees = self.shipping_charged = self.shipping_cost = self.profit = ZERO

    def add(self, item):
        self.count += 1
        self.sales += gross(item)
        self.fees += fees(item)
        self.shipping_charged += item.shipping_charged
        self.shipping_cost += item.shipping_cost
        self.profit += item.profit
        return self

    @classmethod
    def of(cls, sold):
        totals = cls()
        for item in sold:
            totals.add(item)
        return totals

    @property
    def margin(self):
        return pct(self.profit, self.sales)


def totals_by(sold, key):
    groups = defaultdict(Totals)
    for item in sold:
        for name in key(item):
            groups[name].add(item)
    return sorted(groups.items(), key=lambda pair: pair[1].profit, reverse=True)


# ---------------------------------------------------------------- sales & performance

def sales_summary(start, end, group):
    sold = sold_between(start, end)
    report = Report("report-sales-summary", period=period_text(start, end))
    total = Totals()
    by_bucket = defaultdict(Totals)
    for item in sold:
        total.add(item)
        by_bucket[bucket_start(item.sale_date, group)].add(item)
    report.stats = [
        Stat("GROSS SALES", total.sales, foot="Sale prices plus shipping buyers paid"),
        Stat("ITEMS SOLD", total.count, "int"),
        Stat("AVERAGE SALE", average(total.sales, total.count)),
        Stat("PROFIT ON ITEMS", total.profit, "signed", "After cost, fees and shipping"),
    ]
    table = Table(f"By {group}", [(group.title(), "text"), ("Items sold", "int"), ("Gross sales", "money"),
                                  ("Average sale", "money"), ("Profit", "signed")])
    keys = sorted(by_bucket) if group == "day" else buckets(start, end, group)
    for key in keys:
        t = by_bucket.get(key) or Totals()
        table.add(bucket_label(key, group), t.count, t.sales, average(t.sales, t.count), t.profit)
    if sold:
        table.add("Total", total.count, total.sales, average(total.sales, total.count), total.profit, style="total")
    else:
        table.rows = []
    report.tables = [table]
    return report


def sales_by_platform(start, end):
    sold = sold_between(start, end)
    report = Report("report-sales-by-platform", period=period_text(start, end))
    groups = totals_by(sold, lambda item: [item.platform.name])
    total = Totals.of(sold)
    best = groups[0] if groups else None
    report.stats = [
        Stat("GROSS SALES", total.sales),
        Stat("SITE FEES", total.fees, foot=f"{pct(total.fees, total.sales) or 0}% of sales"),
        Stat("PROFIT", total.profit, "signed"),
        Stat("MOST PROFITABLE SITE", best[0] if best else "—", "text", f"{money(best[1].profit)} profit" if best else ""),
    ]
    table = Table("Each site", [("Site", "text"), ("Items", "int"), ("Gross sales", "money"), ("Fees", "money"),
                                ("Shipping +/−", "signed"), ("Profit", "signed"), ("Margin", "pct")],
                  note="Margin is profit as a share of gross sales.")
    for name, t in groups:
        table.add(name, t.count, t.sales, t.fees, t.shipping_charged - t.shipping_cost, t.profit, t.margin)
    if groups:
        table.add("Total", total.count, total.sales, total.fees, total.shipping_charged - total.shipping_cost,
                  total.profit, total.margin, style="total")
    report.tables = [table]
    return report


def sales_by_category(start, end):
    sold = sold_between(start, end)
    report = Report("report-sales-by-category", period=period_text(start, end))
    columns = [("Items", "int"), ("Gross sales", "money"), ("Profit", "signed"), ("Average profit", "signed"),
               ("Margin", "pct")]
    mains = Table("By main type", [("Main type", "text")] + columns,
                  note="Sub-types are added into their main type.")
    for name, t in totals_by(sold, main_category_names):
        mains.add(name, t.count, t.sales, t.profit, average(t.profit, t.count), t.margin)
    categories = Table("By sub-type", [("Sub-type", "text")] + columns,
                       note="An item with more than one sub-type is counted in each of them.")
    for name, t in totals_by(sold, category_names):
        categories.add(name, t.count, t.sales, t.profit, average(t.profit, t.count), t.margin)
    brands = Table("By brand", [("Brand", "text")] + columns)
    for name, t in totals_by(sold, lambda item: [item.brand.name if item.brand else "No brand"]):
        brands.add(name, t.count, t.sales, t.profit, average(t.profit, t.count), t.margin)
    top_category = categories.rows[0].cells[0] if categories.rows else "—"
    top_brand = brands.rows[0].cells[0] if brands.rows else "—"
    report.stats = [Stat("ITEMS SOLD", len(sold), "int"), Stat("PROFIT", money_sum(i.profit for i in sold), "signed"),
                    Stat("TOP CATEGORY", top_category, "text", "By total profit"),
                    Stat("TOP BRAND", top_brand, "text", "By total profit")]
    report.tables = [mains, categories, brands]
    return report


def sold_items(start, end):
    sold = sold_between(start, end)
    report = Report("report-sold-items", period=period_text(start, end))
    table = Table("Every sale", [("Item", "text"), ("Sold", "date"), ("Site", "text"), ("Sale price", "money"),
                                 ("Cost", "money"), ("Fees", "money"), ("Shipping +/−", "signed"),
                                 ("Other costs", "money"), ("Profit", "signed"), ("ROI", "pct"), ("Margin", "pct")],
                  note="Other costs are packaging, promoted listings and money lost on earlier returns of the item.")
    total = Totals.of(sold)
    for item in sold:
        table.add(item_link(item), item.sale_date, item.platform.name, item.sale_price, item.purchase_price, fees(item),
                  item.shipping_charged - item.shipping_cost, item.other_sale_costs + item.return_cost,
                  item.profit, item.roi_percent, item.margin_percent)
    if sold:
        table.add("Total", "", "", money_sum(i.sale_price for i in sold), money_sum(i.purchase_price for i in sold),
                  total.fees, total.shipping_charged - total.shipping_cost,
                  money_sum(i.other_sale_costs + i.return_cost for i in sold), total.profit,
                  pct(total.profit, money_sum(i.purchase_price for i in sold)),
                  pct(total.profit, money_sum(i.sale_price for i in sold)), style="total")
    report.stats = [Stat("ITEMS SOLD", total.count, "int"), Stat("PROFIT", total.profit, "signed"),
                    Stat("AVERAGE PROFIT", average(total.profit, total.count), "signed"),
                    Stat("AVERAGE ROI", pct(total.profit, money_sum(i.purchase_price for i in sold)), "pct",
                         "Profit ÷ what you paid")]
    report.tables = [table]
    return report


def sourcing(start, end):
    bought = list(items().filter(purchase_date__range=(start, end)))
    report = Report("report-sourcing", period=period_text(start, end))
    report.notes = ["Based on items bought in these dates. Profit counts items sold so far, minus items written off."]

    def table(title, label, key):
        groups = defaultdict(list)
        for item in bought:
            groups[key(item)].append(item)
        t = Table(title, [(label, "text"), ("Items bought", "int"), ("Spent", "money"), ("Sold", "int"),
                          ("Sell-through", "pct"), ("Profit", "signed"), ("ROI", "pct")])
        rows = []
        for name, group in groups.items():
            done = [i for i in group if i.status in (Item.SOLD, Item.WRITTEN_OFF)]
            profit = money_sum(i.profit for i in done)
            sold_count = sum(1 for i in group if i.status == Item.SOLD)
            rows.append((profit, [name, len(group), money_sum(i.purchase_price for i in group), sold_count,
                                  pct(sold_count, len(group)), profit, pct(profit, money_sum(realized_cost(i) for i in done))]))
        for _, cells in sorted(rows, key=lambda pair: pair[0], reverse=True):
            t.add(*cells)
        return t

    kinds = table("By type of place", "Type", lambda i: i.source.get_kind_display() if i.source else "Not recorded")
    places = table("By place", "Place", lambda i: i.source.name if i.source else "Not recorded")
    done = [i for i in bought if i.status in (Item.SOLD, Item.WRITTEN_OFF)]
    profit = money_sum(i.profit for i in done)
    report.stats = [Stat("ITEMS BOUGHT", len(bought), "int"), Stat("SPENT", money_sum(i.purchase_price for i in bought)),
                    Stat("PROFIT SO FAR", profit, "signed"),
                    Stat("ROI SO FAR", pct(profit, money_sum(realized_cost(i) for i in done)), "pct")]
    report.tables = [kinds, places]
    return report


def sell_through(start, end):
    listed = list(items().annotate(first_listed=Min("listings__listed_date"))
                  .filter(first_listed__range=(start, end)))
    report = Report("report-sell-through", period=period_text(start, end))
    report.notes = ["Counts items first listed in these dates and how many of them have sold since."]
    columns = [("Listed", "int"), ("Sold", "int"), ("Sell-through", "pct")]

    def add_rows(table, groups):
        for name, group in groups:
            sold_count = sum(1 for i in group if i.status == Item.SOLD)
            table.add(name, len(group), sold_count, pct(sold_count, len(group)))

    by_month = defaultdict(list)
    for item in listed:
        by_month[bucket_start(item.first_listed, "month")].append(item)
    months = Table("By month listed", [("Month", "text")] + columns)
    add_rows(months, [(bucket_label(key, "month"), by_month[key]) for key in sorted(by_month)])

    sites = Table("By site", [("Site", "text")] + columns,
                  note="Each listing counts once per site; “sold” means it sold on that site.")
    for platform in Platform.objects.all():
        listings = list(Listing.objects.filter(platform=platform, listed_date__range=(start, end)))
        if listings:
            sold_count = sum(1 for listing in listings if listing.status == Listing.SOLD)
            sites.add(platform.name, len(listings), sold_count, pct(sold_count, len(listings)))

    by_category = defaultdict(list)
    for item in listed:
        for name in category_names(item):
            by_category[name].append(item)
    categories = Table("By category", [("Category", "text")] + columns)
    add_rows(categories, sorted(by_category.items(), key=lambda pair: -len(pair[1])))

    sold_count = sum(1 for i in listed if i.status == Item.SOLD)
    report.stats = [Stat("ITEMS LISTED", len(listed), "int"), Stat("SOLD SO FAR", sold_count, "int"),
                    Stat("SELL-THROUGH", pct(sold_count, len(listed)), "pct", "Sold ÷ listed")]
    report.tables = [months, sites, categories]
    return report


def days_to_sell(start, end):
    sold = list(items().filter(status=Item.SOLD, sale_date__range=(start, end))
                .annotate(first_listed=Min("listings__listed_date")))
    report = Report("report-days-to-sell", period=period_text(start, end))

    def from_listing(item):
        return (item.sale_date - item.first_listed).days if item.first_listed else None

    columns = [("Items sold", "int"), ("Days from buying", "days"), ("Days from listing", "days")]

    def table(title, label, key):
        groups = defaultdict(list)
        for item in sold:
            for name in key(item):
                groups[name].append(item)
        t = Table(title, [(label, "text")] + columns)
        for name, group in sorted(groups.items(), key=lambda pair: average_days(i.days_to_sell for i in pair[1])):
            t.add(name, len(group), average_days(i.days_to_sell for i in group),
                  average_days(from_listing(i) for i in group))
        return t

    spread = Table("How long sales took", [("Days from buying", "text"), ("Items", "int"), ("Share", "pct")])
    for label, low, high in [("A week or less", 0, 7), ("8–30 days", 8, 30), ("31–90 days", 31, 90),
                             ("91–180 days", 91, 180), ("Over 180 days", 181, None)]:
        count = sum(1 for i in sold if i.days_to_sell >= low and (high is None or i.days_to_sell <= high))
        spread.add(label, count, pct(count, len(sold)))
    if not sold:
        spread.rows = []
    report.stats = [Stat("ITEMS SOLD", len(sold), "int"),
                    Stat("FROM BUYING", average_days(i.days_to_sell for i in sold), "days", "Average days to sell"),
                    Stat("FROM LISTING", average_days(from_listing(i) for i in sold), "days", "Average days to sell")]
    report.tables = [spread, table("By site", "Site", lambda i: [i.platform.name]),
                     table("By category", "Category", category_names)]
    return report


def returns(start, end):
    returned = list(items().filter(return_date__range=(start, end)).order_by("return_date", "pk"))
    sold_count = Item.objects.filter(sale_date__range=(start, end)).count()
    report = Report("report-returns", period=period_text(start, end))
    lost = money_sum(i.return_cost for i in returned)
    report.stats = [Stat("RETURNS", len(returned), "int"), Stat("MONEY LOST", lost, foot="Return labels, fees kept, restocking"),
                    Stat("RETURN RATE", pct(len(returned), sold_count), "pct", "Returns ÷ sales in these dates")]
    reasons = defaultdict(list)
    for item in returned:
        reasons[item.return_reason.strip() or "No reason given"].append(item)
    reason_table = Table("Reasons", [("Reason", "text"), ("Returns", "int"), ("Money lost", "money")])
    for reason, group in sorted(reasons.items(), key=lambda pair: -len(pair[1])):
        reason_table.add(reason, len(group), money_sum(i.return_cost for i in group))
    each = Table("Each return", [("Item", "text"), ("Came back", "date"), ("Sold on", "text"), ("Reason", "text"),
                                 ("Money lost", "money")])
    for item in returned:
        each.add(item_link(item), item.return_date, item.platform.name if item.platform else "—",
                 item.return_reason or "—", item.return_cost)
    report.tables = [reason_table, each]
    return report


def top_bottom(start, end, count=10):
    done = sold_between(start, end) + written_off_between(start, end)
    report = Report("report-top-bottom", period=period_text(start, end))
    columns = [("Item", "text"), ("Result", "text"), ("Date", "date"), ("Cost", "money"), ("Profit", "signed"),
               ("ROI", "pct")]

    def table(title, picked):
        t = Table(title, columns)
        for item in picked:
            sold = item.status == Item.SOLD
            t.add(item_link(item), f"Sold on {item.platform}" if sold else "Written off",
                  item.sale_date if sold else item.written_off_date, item.purchase_price, item.profit, item.roi_percent)
        return t

    ranked = sorted(done, key=lambda item: item.profit, reverse=True)
    losers = [item for item in reversed(ranked) if item.profit < 0][:count]
    categories = Table("Categories, worst first", [("Category", "text"), ("Items", "int"), ("Average profit", "signed"),
                                                   ("ROI", "pct"), ("Money-losers", "int")],
                       note="Categories at the top earn the least per item — think twice before buying more.")
    groups = defaultdict(list)
    for item in done:
        for name in category_names(item):
            groups[name].append(item)
    for name, group in sorted(groups.items(), key=lambda pair: average(money_sum(i.profit for i in pair[1]), len(pair[1]))):
        profit = money_sum(i.profit for i in group)
        categories.add(name, len(group), average(profit, len(group)), pct(profit, money_sum(realized_cost(i) for i in group)),
                       sum(1 for i in group if i.profit < 0))
    report.stats = [Stat("BEST FLIP", ranked[0].profit if ranked else None, "signed", ranked[0].title if ranked else ""),
                    Stat("MONEY-LOSERS", len([i for i in done if i.profit < 0]), "int",
                         f"Lost {money(abs(money_sum(i.profit for i in losers)))} in total" if losers else "None — nice!")]
    report.tables = [table(f"Top {count}", ranked[:count]), table("Money-losers", losers), categories]
    report.tables[1].empty = "No money-losers in these dates."
    return report


# ---------------------------------------------------------------- inventory

def inventory_on_hand():
    unsold = unsold_items()
    report = Report("report-inventory", period=f"As of {nice_date(timezone.localdate())}")
    cost = money_sum(i.purchase_price for i in unsold)
    report.stats = [Stat("ITEMS ON HAND", len(unsold), "int"), Stat("TOTAL COST", cost, foot="What you paid for them"),
                    Stat("AVERAGE COST", average(cost, len(unsold)))]

    def table(title, label, key):
        groups = defaultdict(list)
        for item in unsold:
            for name in key(item):
                groups[name].append(item)
        t = Table(title, [(label, "text"), ("Items", "int"), ("Cost", "money"), ("Share of cost", "pct")])
        for name, group in sorted(groups.items(), key=lambda pair: -money_sum(i.purchase_price for i in pair[1])):
            group_cost = money_sum(i.purchase_price for i in group)
            t.add(name, len(group), group_cost, pct(group_cost, cost))
        return t

    status = table("By status", "Status", lambda i: [i.get_status_display()])
    if unsold:
        status.add("Total", len(unsold), cost, Decimal("100.0") if cost else None, style="total")
    report.tables = [status, table("By category", "Category", category_names),
                     table("By type of place bought", "Type", lambda i: [i.source.get_kind_display() if i.source else "Not recorded"])]
    report.tables[1].note = "An item in more than one category is counted in each of them."
    report.tables[0].empty = report.tables[1].empty = report.tables[2].empty = "No unsold items."
    return report


def aged_inventory():
    unsold = unsold_items()
    report = Report("report-aged-inventory", period=f"As of {nice_date(timezone.localdate())}")
    cost = money_sum(i.purchase_price for i in unsold)
    ages = Table("By age", [("Held for", "text"), ("Items", "int"), ("Cost", "money"), ("Share of cost", "pct")],
                 empty="No unsold items.")
    for label, low, high in [("Under 90 days", 0, 89), ("90–179 days", 90, 179), ("180–364 days", 180, 364),
                             ("365 days or more", 365, None)]:
        group = [i for i in unsold if i.days_in_inventory >= low and (high is None or i.days_in_inventory <= high)]
        group_cost = money_sum(i.purchase_price for i in group)
        ages.add(label, len(group), group_cost, pct(group_cost, cost))
    if not unsold:
        ages.rows = []
    old = [i for i in unsold if i.days_in_inventory >= 90]
    items_table = Table("Held 90 days or more, oldest first",
                        [("Item", "text"), ("Bought", "date"), ("Days held", "int"), ("Cost", "money"),
                         ("Status", "text"), ("Stored", "text")], empty="Nothing has been sitting for 90 days. 🎉")
    for item in old:
        items_table.add(item_link(item), item.purchase_date, item.days_in_inventory, item.purchase_price,
                        item.get_status_display(), item.storage_location or "—")
    old_cost = money_sum(i.purchase_price for i in old)
    report.stats = [Stat("HELD 90+ DAYS", len(old), "int", f"of {len(unsold)} unsold items"),
                    Stat("COST TIED UP", old_cost, foot=f"{pct(old_cost, cost) or 0}% of your inventory cost"),
                    Stat("HELD 365+ DAYS", sum(1 for i in unsold if i.days_in_inventory >= 365), "int")]
    report.tables = [ages, items_table]
    return report


def unlisted_backlog():
    waiting = [i for i in unsold_items() if i.status in Item.NOT_LISTED]
    report = Report("report-unlisted", period=f"As of {nice_date(timezone.localdate())}")
    cost = money_sum(i.purchase_price for i in waiting)
    report.stats = [Stat("NOT LISTED YET", len(waiting), "int"), Stat("MONEY TIED UP", cost),
                    Stat("WAITING THE LONGEST", waiting[0].days_in_inventory if waiting else None, "days",
                         waiting[0].title if waiting else "")]
    table = Table("Waiting to be listed, oldest first", [("Item", "text"), ("Bought", "date"), ("Days waiting", "int"),
                                                        ("Cost", "money"), ("Status", "text"), ("Stored", "text")],
                  empty="Everything you own is listed. Nice work!")
    for item in waiting:
        table.add(item_link(item), item.purchase_date, item.days_in_inventory, item.purchase_price,
                  "Came back (return)" if item.status == Item.RETURNED else "Never listed", item.storage_location or "—")
    if waiting:
        table.add("Total", "", "", cost, "", "", style="total")
    report.tables = [table]
    return report


def locations():
    unsold = sorted(unsold_items(), key=lambda i: ((i.storage_location or "~").lower(), i.sku or "", i.title.lower()))
    report = Report("report-locations", period=f"As of {nice_date(timezone.localdate())}")
    table = Table("Where everything is", [("Item", "text"), ("Item code", "text"), ("Status", "text"),
                                          ("Days in stock", "int"), ("Cost", "money")], empty="No unsold items.")
    groups = defaultdict(list)
    for item in unsold:
        groups[item.storage_location or "No location set"].append(item)
    for place, group in groups.items():
        table.add(f"{place} · {len(group)} item{'s' if len(group) != 1 else ''}", "", "", "", "", style="head")
        for item in group:
            table.add(item_link(item), item.sku or "—", item.get_status_display(), item.days_in_inventory, item.purchase_price)
    # Sold but still on the shelf: pull these to pack. Oldest sale first. Tapping "I shipped it" frees the slot.
    today = timezone.localdate()
    to_ship = list(items().filter(status=Item.SOLD, location_released_at__isnull=True).order_by("sale_date", "pk"))
    ship_table = Table("To ship", [("Item", "text"), ("Location", "text"), ("SKU", "text"), ("Sold on", "text"),
                                   ("Sale date", "date"), ("Days waiting", "int")],
                       note="Sold but still on the shelf. Tap “I shipped it” on the item once it's sent to free its slot.",
                       empty="Nothing waiting to ship.")
    for item in to_ship:
        ship_table.add(item_link(item), item.storage_location or "No location set", item.sku or "—", str(item.platform),
                       item.sale_date, (today - item.sale_date).days)
    no_place = len(groups.get("No location set", []))
    report.stats = [Stat("TO SHIP", len(to_ship), "int"), Stat("UNSOLD ITEMS", len(unsold), "int"),
                    Stat("LOCATIONS", len(groups) - (1 if no_place else 0), "int"),
                    Stat("NO LOCATION SET", no_place, "int", "Add one when you edit the item")]
    report.tables = [ship_table, table]
    return report


def inventory_values(year):
    """Schedule C Part III numbers for one year. Ending inventory is as of today for the current year."""
    today = timezone.localdate()
    begin_day, end_day = date(year - 1, 12, 31), min(date(year, 12, 31), today)
    every = list(Item.objects.filter(purchase_date__lte=end_day))
    beginning = [i for i in every if in_stock_on(i, begin_day)]
    bought = [i for i in every if i.purchase_date.year == year]
    ending = [i for i in every if in_stock_on(i, end_day)]
    values = {
        "beginning": money_sum(i.purchase_price for i in beginning), "beginning_count": len(beginning),
        "purchases": money_sum(i.purchase_price for i in bought), "purchases_count": len(bought),
        "ending": money_sum(i.purchase_price for i in ending), "ending_count": len(ending),
        "end_day": end_day,
    }
    values["cogs"] = values["beginning"] + values["purchases"] - values["ending"]
    return values


def cogs(year):
    v = inventory_values(year)
    so_far = v["end_day"] < date(year, 12, 31)
    report = Report("report-cogs", period=f"{year}" + (f" so far (to {nice_date(v['end_day'])})" if so_far else ""))
    report.stats = [Stat("BEGINNING INVENTORY", v["beginning"], foot=f"{v['beginning_count']} items on Jan 1"),
                    Stat("PURCHASES", v["purchases"], foot=f"{v['purchases_count']} items bought"),
                    Stat("ENDING INVENTORY", v["ending"], foot=f"{v['ending_count']} items on {nice_date(v['end_day'])}"),
                    Stat("COST OF GOODS SOLD", v["cogs"])]
    table = Table("Cost of goods sold (Schedule C, Part III)", [("Line", "text"), ("", "text"), ("Amount", "money")],
                  statement=True,
                  note="Inventory is valued at what you paid (cost). Items written off leave inventory, so their "
                       "cost is included in cost of goods sold.")
    table.add("35", f"Inventory at beginning of year ({v['beginning_count']} items)", v["beginning"])
    table.add("36", f"Purchases ({v['purchases_count']} items)", v["purchases"])
    table.add("40", "Add lines 35 and 36", v["beginning"] + v["purchases"], style="sub")
    table.add("41", f"Inventory at end of year ({v['ending_count']} items)", v["ending"])
    table.add("42", "Cost of goods sold", v["cogs"], style="total")
    start, end = date(year, 1, 1), date(year, 12, 31)
    sold, written_off = sold_between(start, end), written_off_between(start, end)
    check = Table("Where the cost went", [("", "text"), ("Items", "int"), ("Cost", "money")], statement=True)
    check.add("Items sold", len(sold), money_sum(i.purchase_price for i in sold))
    check.add("Items written off", len(written_off), money_sum(i.purchase_price for i in written_off))
    report.tables = [table, check]
    return report


# ---------------------------------------------------------------- purchases & expenses

def purchases(start, end):
    bought = list(items().filter(purchase_date__range=(start, end)).order_by("purchase_date", "pk"))
    report = Report("report-purchases", period=period_text(start, end))
    trips = defaultdict(list)
    for item in bought:
        trips[(item.purchase_date, item.source)].append(item)
    table = Table("Buying trips", [("Place", "text"), ("Date", "date"), ("Type", "text"), ("Items", "int"),
                                   ("Spent", "money"), ("What you bought", "text")],
                  note="Items bought at the same place on the same day count as one trip.")
    for (day, source), group in trips.items():
        names = ", ".join(i.title for i in group[:4]) + (f" and {len(group) - 4} more" if len(group) > 4 else "")
        table.add(source.name if source else "Not recorded", day, source.get_kind_display() if source else "—",
                  len(group), money_sum(i.purchase_price for i in group), names)
    spent = money_sum(i.purchase_price for i in bought)
    if bought:
        table.add("Total", "", "", len(bought), spent, "", style="total")
    report.stats = [Stat("SPENT", spent), Stat("ITEMS BOUGHT", len(bought), "int"), Stat("TRIPS", len(trips), "int"),
                    Stat("AVERAGE PER ITEM", average(spent, len(bought)))]
    report.tables = [table]
    return report


def selling_costs(sold):
    """Costs that come out of each sale rather than being entered as expenses, with their Schedule C line."""
    return [("10", "Commissions and fees", "Site fees (from your sales)", money_sum(fees(i) for i in sold)),
            ("27a", "Other expenses", "Shipping labels (from your sales)", money_sum(i.shipping_cost for i in sold)),
            ("27a", "Other expenses", "Packaging & promoted listings (from your sales)",
             money_sum(i.other_sale_costs for i in sold))]


def expense_lines(start, end):
    """[(line, line name, what, amount)] for expenses and selling costs in the dates, in Schedule C order."""
    totals = defaultdict(lambda: ZERO)
    for expense in expenses_between(start, end):
        totals[expense.category] += expense.amount
    labels = dict(Expense.CATEGORY_CHOICES)
    lines = [(*SCHEDULE_C[category], labels[category], amount) for category, amount in totals.items()]
    lines += [line for line in selling_costs(sold_between(start, end)) if line[3]]
    return sorted(lines, key=lambda line: (LINE_ORDER.index(line[0]), line[2]))


def expenses_report(start, end):
    report = Report("report-expenses", period=period_text(start, end))
    lines = expense_lines(start, end)
    table = Table("By Schedule C line", [("What", "text"), ("Line", "text"), ("Schedule C line", "text"), ("Amount", "money")],
                  note="Equipment usually goes on line 13 (depreciation / section 179), or line 22 if it's small — "
                       "ask your tax preparer.")
    for line, name, what, amount in lines:
        table.add(what, line, name, amount)
    total = money_sum(line[3] for line in lines)
    if lines:
        table.add("Total", "", "", total, style="total")
    expenses = expenses_between(start, end)
    each = Table("Every expense", [("What", "text"), ("Date", "date"), ("Category", "text"), ("Receipt", "text"),
                                   ("Amount", "money")])
    for expense in expenses:
        receipt = Link("View", expense.receipt.url) if expense.receipt else "—"
        each.add(expense.description or expense.get_category_display(), expense.date, expense.get_category_display(),
                 receipt, expense.amount)
    report.stats = [Stat("TOTAL", total, foot="Expenses plus selling costs from sales"),
                    Stat("EXPENSES ENTERED", money_sum(e.amount for e in expenses), foot=f"{len(expenses)} expenses"),
                    Stat("WITH A RECEIPT", sum(1 for e in expenses if e.receipt), "int", f"of {len(expenses)} expenses")]
    report.tables = [table, each]
    return report


def platform_fees(start, end):
    sold = sold_between(start, end)
    report = Report("report-platform-fees", period=period_text(start, end))
    table = Table("Each site", [("Site", "text"), ("Sales", "int"), ("Gross sales", "money"), ("Fees", "money"),
                                ("Fees % of sales", "pct"), ("Average fee", "money")])
    groups = sorted(totals_by(sold, lambda i: [i.platform.name]), key=lambda pair: -pair[1].fees)
    for name, t in groups:
        table.add(name, t.count, t.sales, t.fees, pct(t.fees, t.sales), average(t.fees, t.count))
    total = Totals.of(sold)
    if sold:
        table.add("Total", total.count, total.sales, total.fees, pct(total.fees, total.sales),
                  average(total.fees, total.count), style="total")
    report.stats = [Stat("FEES PAID", total.fees), Stat("SHARE OF SALES", pct(total.fees, total.sales), "pct"),
                    Stat("AVERAGE PER SALE", average(total.fees, total.count))]
    report.tables = [table]
    return report


def shipping(start, end):
    sold = sold_between(start, end)
    report = Report("report-shipping", period=period_text(start, end))
    columns = [("Orders", "int"), ("Buyers paid", "money"), ("Labels cost", "money"), ("Shipping +/−", "signed"),
               ("Average +/−", "signed")]
    sites = Table("By site", [("Site", "text")] + columns)
    for name, t in totals_by(sold, lambda i: [i.platform.name]):
        net = t.shipping_charged - t.shipping_cost
        sites.add(name, t.count, t.shipping_charged, t.shipping_cost, net, average(net, t.count))
    months = Table("By month", [("Month", "text")] + columns)
    by_month = defaultdict(Totals)
    for item in sold:
        by_month[bucket_start(item.sale_date, "month")].add(item)
    for key in sorted(by_month):
        t = by_month[key]
        net = t.shipping_charged - t.shipping_cost
        months.add(bucket_label(key, "month"), t.count, t.shipping_charged, t.shipping_cost, net, average(net, t.count))
    losers = sorted((i for i in sold if i.shipping_cost > i.shipping_charged),
                    key=lambda i: i.shipping_charged - i.shipping_cost)
    lost = Table("Orders where the label cost more than the buyer paid", [("Item", "text"), ("Sold", "date"),
                 ("Site", "text"), ("Buyers paid", "money"), ("Label", "money"), ("Lost", "signed")],
                 empty="No order lost money on shipping.")
    for item in losers:
        lost.add(item_link(item), item.sale_date, item.platform.name, item.shipping_charged, item.shipping_cost,
                 item.shipping_charged - item.shipping_cost)
    charged, cost = money_sum(i.shipping_charged for i in sold), money_sum(i.shipping_cost for i in sold)
    report.stats = [Stat("BUYERS PAID", charged), Stat("LABELS COST", cost),
                    Stat("SHIPPING +/−", charged - cost, "signed", "Positive means shipping made you money"),
                    Stat("ORDERS THAT LOST", len(losers), "int", f"of {len(sold)} orders")]
    report.tables = [sites, months, lost]
    return report


def mileage(start, end):
    trips = [e for e in expenses_between(start, end) if e.category == Expense.MILEAGE]
    report = Report("report-mileage", period=period_text(start, end))
    table = Table("Every trip", [("Trip", "text"), ("Date", "date"), ("Miles", "miles"), ("Rate", "text"),
                                 ("Deduction", "money")],
                  empty="No mileage in these dates. Add trips under Expenses → Mileage / travel.")
    for trip in trips:
        table.add(trip.description or "Business trip", trip.date, trip.miles, f"${trip.mileage_rate}/mi", trip.amount)
    miles = sum((t.miles for t in trips), Decimal("0"))
    deduction = money_sum(t.amount for t in trips)
    if trips:
        table.add("Total", "", miles, "", deduction, style="total")
    report.stats = [Stat("BUSINESS MILES", miles, "miles"), Stat("DEDUCTION", deduction), Stat("TRIPS", len(trips), "int")]
    report.tables = [table]
    return report


# ---------------------------------------------------------------- accounting & tax

def money_by_bucket(rows, group):
    """rows: [(date, amount)] → {bucket start: total}"""
    totals = defaultdict(lambda: ZERO)
    for day, amount in rows:
        totals[bucket_start(day, group)] += amount or ZERO
    return totals


def profit_loss_lines(start, end, group):
    """[(label, style, {bucket: amount})] — the profit & loss statement, shared with the tax worksheet."""
    sold, written_off = sold_between(start, end), written_off_between(start, end)
    returned = list(Item.objects.filter(return_date__range=(start, end)))
    expenses = expenses_between(start, end)

    def by(rows):
        return money_by_bucket(rows, group)

    def combine(sign_parts):
        totals = defaultdict(lambda: ZERO)
        for sign, part in sign_parts:
            for key, amount in part.items():
                totals[key] += sign * amount
        return totals

    sales = by((i.sale_date, i.sale_price) for i in sold)
    shipping_in = by((i.sale_date, i.shipping_charged) for i in sold)
    income = combine([(1, sales), (1, shipping_in)])
    cost_sold = by((i.sale_date, i.purchase_price) for i in sold)
    cost_written_off = by((i.written_off_date, i.purchase_price) for i in written_off)
    cogs_total = combine([(1, cost_sold), (1, cost_written_off)])
    gross_profit = combine([(1, income), (-1, cogs_total)])
    site_fees = by((i.sale_date, fees(i)) for i in sold)
    labels = by((i.sale_date, i.shipping_cost) for i in sold)
    other = by((i.sale_date, i.other_sale_costs) for i in sold)
    lost_returns = by((i.return_date, i.return_cost) for i in returned)
    selling = combine([(1, site_fees), (1, labels), (1, other), (1, lost_returns)])
    category_lines, names = [], dict(Expense.CATEGORY_CHOICES)
    for category, _ in Expense.CATEGORY_CHOICES:
        part = by((e.date, e.amount) for e in expenses if e.category == category)
        if part:
            category_lines.append((names[category], "", part))
    overhead = combine([(1, part) for _, _, part in category_lines])
    net = combine([(1, gross_profit), (-1, selling), (-1, overhead)])
    return [
        ("Income", "head", {}), ("Sales", "", sales), ("Shipping buyers paid", "", shipping_in),
        ("Total income", "sub", income),
        ("Cost of goods sold", "head", {}), ("Cost of items sold", "", cost_sold),
        ("Items written off", "", cost_written_off), ("Total cost of goods sold", "sub", cogs_total),
        ("Gross profit", "sub", gross_profit),
        ("Selling costs", "head", {}), ("Site fees", "", site_fees), ("Shipping labels", "", labels),
        ("Packaging & promoted listings", "", other), ("Lost on returns", "", lost_returns),
        ("Total selling costs", "sub", selling),
        ("Operating expenses", "head", {}), *category_lines, ("Total operating expenses", "sub", overhead),
        ("Net profit", "total", net),
    ]


def profit_loss(start, end, group):
    report = Report("report-profit-loss", period=period_text(start, end))
    keys = buckets(start, end, group)
    show_total = len(keys) > 1
    columns = [("", "text")] + [(bucket_label(key, group), "money") for key in keys]
    if show_total:
        columns.append(("Total", "money"))
    table = Table("Profit & loss statement", columns, statement=True,
                  note="Returns count when the item came back. Mileage is included at the standard rate.")
    totals = {}
    for label, style, part in profit_loss_lines(start, end, group):
        if style == "head":
            table.add(label, *[""] * (len(columns) - 1), style="head")
            continue
        values = [part.get(key, ZERO) for key in keys]
        totals[label] = sum(values, ZERO)
        if show_total:
            values.append(totals[label])
        table.add(label, *values, style=style)
    net = totals["Net profit"]
    report.stats = [Stat("TOTAL INCOME", totals["Total income"]), Stat("GROSS PROFIT", totals["Gross profit"], "signed"),
                    Stat("EXPENSES & SELLING COSTS", totals["Total selling costs"] + totals["Total operating expenses"]),
                    Stat("NET PROFIT", net, "signed")]
    report.tables = [table]
    return report


def cash_flow(start, end, group):
    report = Report("report-cash-flow", period=period_text(start, end))
    sold = sold_between(start, end)
    returned = list(Item.objects.filter(return_date__range=(start, end)))
    bought = list(Item.objects.filter(purchase_date__range=(start, end)))
    spent = [e for e in expenses_between(start, end) if e.category != Expense.MILEAGE]
    money_in = money_by_bucket(((i.sale_date, gross(i)) for i in sold), group)
    inventory = money_by_bucket(((i.purchase_date, i.purchase_price) for i in bought), group)
    selling = money_by_bucket([(i.sale_date, fees(i) + i.shipping_cost + i.other_sale_costs) for i in sold]
                              + [(i.return_date, i.return_cost) for i in returned], group)
    overhead = money_by_bucket(((e.date, e.amount) for e in spent), group)
    table = Table(f"By {group}", [(group.title(), "text"), ("Money in", "money"), ("Buying inventory", "money"),
                                  ("Selling costs", "money"), ("Expenses", "money"), ("Net", "signed"),
                                  ("Running total", "signed")],
                  note="Money in is what buyers paid. Selling costs are site fees, labels, packaging and returns. "
                       "Mileage isn't cash you spent, so it's left out here.")
    running = ZERO
    sums = [ZERO] * 4
    for key in buckets(start, end, group):
        values = [money_in.get(key, ZERO), inventory.get(key, ZERO), selling.get(key, ZERO), overhead.get(key, ZERO)]
        net = values[0] - values[1] - values[2] - values[3]
        running += net
        sums = [a + b for a, b in zip(sums, values)]
        table.add(bucket_label(key, group), *values, net, running)
    table.add("Total", *sums, running, running, style="total")
    report.stats = [Stat("MONEY IN", sums[0]), Stat("MONEY OUT", sums[1] + sums[2] + sums[3]),
                    Stat("NET CASH", running, "signed", "Positive means more came in than went out")]
    report.tables = [table]
    return report


def sales_tax(start, end):
    sold = sold_between(start, end)
    report = Report("report-sales-tax", period=period_text(start, end))
    marketplace = [i for i in sold if i.platform.collects_sales_tax]
    direct = [i for i in sold if not i.platform.collects_sales_tax]
    sites = Table("By site", [("Site", "text"), ("Who collects tax", "text"), ("Orders", "int"), ("Sales", "money")])
    for name, t in totals_by(sold, lambda i: [i.platform.name]):
        collects = next(i.platform.collects_sales_tax for i in sold if i.platform.name == name)
        sites.add(name, "The site" if collects else "You", t.count, t.sales)
    each = Table("Direct sales (you may need to collect tax)", [("Item", "text"), ("Sold", "date"), ("Site", "text"),
                 ("Sale price", "money"), ("Shipping", "money"), ("Total", "money")],
                 empty="No direct sales in these dates.")
    for item in direct:
        each.add(item_link(item), item.sale_date, item.platform.name, item.sale_price, item.shipping_charged, gross(item))
    report.notes = ["Sales tax rules depend on your state. Change who collects tax for a site under Home → Sites I sell on."]
    report.stats = [Stat("MARKETPLACE-COLLECTED", money_sum(gross(i) for i in marketplace), foot=f"{len(marketplace)} orders"),
                    Stat("DIRECT SALES", money_sum(gross(i) for i in direct), foot=f"{len(direct)} orders — tax may be due")]
    report.tables = [sites, each]
    return report


def net_profit(start, end):
    return dict((label, sum(part.values(), ZERO)) for label, _, part in profit_loss_lines(start, end, "year"))["Net profit"]


def estimated_tax(year, rate):
    today = timezone.localdate()
    start, end = date(year, 1, 1), min(date(year, 12, 31), today)
    report = Report("report-estimated-tax", period=f"{year}, {period_text(start, end)}")
    so_far = net_profit(start, end)
    days, year_days = (end - start).days + 1, (date(year, 12, 31) - start).days + 1
    projected = so_far if days == year_days else (so_far / days * year_days).quantize(CENT)
    taxable_se = max(projected * SE_EARNINGS_SHARE, ZERO).quantize(CENT)
    se_tax = (taxable_se * SE_TAX_RATE).quantize(CENT) if taxable_se >= 400 else ZERO
    half_se = (se_tax / 2).quantize(CENT)
    income_tax = max((projected - half_se) * rate / 100, ZERO).quantize(CENT)
    total = se_tax + income_tax
    quarterly = (total / 4).quantize(CENT)
    table = Table("Worksheet", [("", "text"), ("Amount", "money")], statement=True)
    table.add("Net profit so far", so_far, style="sub")
    table.add(f"Projected profit for {year}" + ("" if days == year_days else f" (so far × {year_days} ÷ {days} days)"), projected)
    table.add("Self-employment tax", "", style="head")
    table.add("Profit subject to self-employment tax (92.35%)", taxable_se)
    table.add("Self-employment tax (15.3%)", se_tax)
    table.add("Income tax", "", style="head")
    table.add("Half of self-employment tax (deductible)", half_se)
    table.add(f"Income tax at {rate}% on projected profit minus that deduction", income_tax)
    table.add(f"Estimated total tax for {year}", total, style="sub")
    table.add("Each quarterly payment (÷ 4)", quarterly, style="total")
    due = Table("Payment due dates", [("Payment", "text"), ("Due", "date"), ("Amount", "money")], statement=True)
    for label, day in [("1st quarter", date(year, 4, 15)), ("2nd quarter", date(year, 6, 15)),
                       ("3rd quarter", date(year, 9, 15)), ("4th quarter", date(year + 1, 1, 15))]:
        due.add(label, day, quarterly)
    report.notes = ["This is an estimate to help you plan, not tax advice. It doesn't include other income, deductions "
                    "like the standard or QBI deduction, or state tax. Due dates move to the next business day when "
                    "they fall on a weekend or holiday."]
    report.stats = [Stat("PROFIT SO FAR", so_far, "signed"), Stat("PROJECTED PROFIT", projected, "signed"),
                    Stat("ESTIMATED TAX", total, foot=f"Self-employment + {rate}% income tax"),
                    Stat("EACH QUARTER", quarterly)]
    report.tables = [table, due]
    return report


def schedule_c(year):
    start, end = date(year, 1, 1), date(year, 12, 31)
    report = Report("report-schedule-c", period=str(year))
    sold = sold_between(start, end)
    receipts = money_sum(gross(i) for i in sold)
    returns_lost = money_sum(i.return_cost for i in Item.objects.filter(return_date__range=(start, end)))
    inventory = inventory_values(year)
    line3 = receipts - returns_lost
    gross_profit = line3 - inventory["cogs"]
    by_line = defaultdict(lambda: ZERO)
    names = {}
    for line, name, _, amount in expense_lines(start, end):
        by_line[line] += amount
        names[line] = name
    home_office = by_line.pop("30", ZERO)
    total_expenses = money_sum(by_line.values())
    tentative = gross_profit - total_expenses
    net = tentative - home_office
    trips = [e for e in expenses_between(start, end) if e.category == Expense.MILEAGE]
    miles = sum((t.miles for t in trips), Decimal("0"))

    table = Table(f"Schedule C summary for {year}", [("Line", "text"), ("", "text"), ("Amount", "money")], statement=True,
                  note="Bring this to your tax preparer or use it to fill in Schedule C. Selling costs taken out of "
                       "your sales (site fees, labels, packaging) are included on lines 10 and 27a.")
    table.add("", "Part I — Income", "", style="head")
    table.add("1", f"Gross receipts ({len(sold)} sales, including shipping buyers paid)", receipts)
    table.add("2", "Returns and allowances (money lost on returns)", returns_lost)
    table.add("3", "Subtract line 2 from line 1", line3, style="sub")
    table.add("4", "Cost of goods sold (line 42)", inventory["cogs"])
    table.add("5", "Gross profit", gross_profit, style="sub")
    table.add("7", "Gross income", gross_profit, style="sub")
    table.add("", "Part II — Expenses", "", style="head")
    for line in LINE_ORDER:
        if line in by_line:
            table.add(line, names[line], by_line[line])
    table.add("28", "Total expenses before business use of home", total_expenses, style="sub")
    table.add("29", "Tentative profit or loss", tentative, style="sub")
    table.add("30", "Business use of home", home_office)
    table.add("31", "Net profit or loss", net, style="total")
    table.add("", "Part III — Cost of goods sold", "", style="head")
    table.add("35", "Inventory at beginning of year", inventory["beginning"])
    table.add("36", "Purchases", inventory["purchases"])
    table.add("40", "Add lines 35 and 36", inventory["beginning"] + inventory["purchases"], style="sub")
    table.add("41", "Inventory at end of year", inventory["ending"])
    table.add("42", "Cost of goods sold", inventory["cogs"], style="sub")
    table.add("", "Part IV — Vehicle", "", style="head")
    table.add("44b", f"Business miles: {miles:,.1f} ({len(trips)} trips) — deduction is on line 9", "")
    report.notes = ["Not tax advice. Check every number with your records or a tax preparer before you file."]
    report.stats = [Stat("GROSS RECEIPTS", receipts), Stat("COST OF GOODS SOLD", inventory["cogs"]),
                    Stat("TOTAL EXPENSES", total_expenses + home_office), Stat("NET PROFIT", net, "signed")]
    report.tables = [table]
    return report
