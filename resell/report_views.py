"""Reports: one view per report. The numbers come from reports.py; these views read the filters (dates, grouping,
year) from the address bar and show the report on screen, or as a PDF when the address ends in ?format=pdf."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from xhtml2pdf import pisa
from . import reports
from .access import resell_required
from .models import Expense, Item

# Date presets above each dated report: (key, label)
PRESETS = [("this_month", "This month"), ("last_month", "Last month"), ("this_quarter", "This quarter"),
           ("ytd", "This year"), ("last_year", "Last year"), ("last_12", "Last 12 months")]
SUMMARY_GROUPS = [("day", "Day"), ("week", "Week"), ("month", "Month")]
STATEMENT_GROUPS = [("month", "Month"), ("quarter", "Quarter"), ("year", "Year")]
DEFAULT_TAX_RATE = Decimal("12")
# The PDF's built-in Helvetica can't draw these, so they're swapped for plain characters
PDF_SAFE = [("−", "-"), ("→", ">"), (" 🎉", ""), ("🎉", "")]


def query(request, **changes):
    """This page's address with some filters changed (None removes one). Always drops ?format=pdf."""
    params = request.GET.copy()
    params.pop("format", None)
    for key, value in changes.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return f"?{params.urlencode()}" if params else "?"


def preset_range(key, today):
    if key == "this_month":
        return today.replace(day=1), today
    if key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if key == "this_quarter":
        return date(today.year, (today.month - 1) // 3 * 3 + 1, 1), today
    if key == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if key == "last_12":  # this month and the 11 before it
        month = today.month - 11
        return date(today.year + (month - 1) // 12, (month - 1) % 12 + 1, 1), today
    return date(today.year, 1, 1), today


def safe_date(text):
    """A typed-in date, or None if it's missing or impossible (e.g. month 13)."""
    try:
        return parse_date(text or "")
    except ValueError:
        return None


def date_filter(request, default="ytd"):
    """(start, end, filter block). Custom dates (?start=&end=) win over the preset tabs."""
    today = timezone.localdate()
    start, end = safe_date(request.GET.get("start")), safe_date(request.GET.get("end"))
    custom = bool(start and end)
    if custom:
        start, end = min(start, end), max(start, end)
        preset = None
    else:
        preset = request.GET.get("period") if request.GET.get("period") in dict(PRESETS) else default
        start, end = preset_range(preset, today)
    tabs = [(label, query(request, period=key, start=None, end=None), key == preset) for key, label in PRESETS]
    return start, end, {"kind": "dates", "label": "Dates", "tabs": tabs, "start": start, "end": end, "custom": custom,
                        "keep": [(k, v) for k, v in request.GET.items() if k not in ("period", "start", "end", "format")]}


def choice_filter(request, param, choices, default, label):
    value = request.GET.get(param) if request.GET.get(param) in dict(choices) else default
    tabs = [(text, query(request, **{param: key}), key == value) for key, text in choices]
    return value, {"kind": "tabs", "label": label, "tabs": tabs}


def year_filter(request):
    today = timezone.localdate()
    first = min([d.year for d in Item.objects.dates("purchase_date", "year")]
                + [d.year for d in Expense.objects.dates("date", "year")] + [today.year])
    years = list(range(today.year, first - 1, -1))
    year = int(request.GET["year"]) if request.GET.get("year", "").isdigit() and int(request.GET["year"]) in years \
        else today.year
    tabs = [(str(y), query(request, year=y), y == year) for y in years]
    return year, {"kind": "tabs", "label": "Year", "tabs": tabs}


def render_report(request, report, *filters):
    if request.GET.get("format") == "pdf":
        html = render_to_string("resell/reports/report_pdf.html",
                                {"report": report, "generated": timezone.localtime()}, request=request)
        for fancy, plain in PDF_SAFE:
            html = html.replace(fancy, plain)
        response = HttpResponse(content_type="application/pdf")
        filename = f"{report.name.removeprefix('report-')}-{timezone.localdate():%Y-%m-%d}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        if pisa.CreatePDF(html, dest=response, encoding="utf-8").err:
            return HttpResponse("Sorry, the PDF couldn't be made. Please try again.", status=500)
        return response
    return render(request, "resell/reports/report.html",
                  {"report": report, "filters": filters, "pdf_url": query(request, format="pdf")})


@resell_required
def report_index(request):
    return render(request, "resell/reports/index.html", {"catalog": reports.CATALOG})


# ---------------------------------------------------------------- sales & performance

@resell_required
def sales_summary(request):
    start, end, dates = date_filter(request)
    group, groups = choice_filter(request, "by", SUMMARY_GROUPS, "month", "Show by")
    return render_report(request, reports.sales_summary(start, end, group), dates, groups)


@resell_required
def sales_by_platform(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.sales_by_platform(start, end), dates)


@resell_required
def sales_by_category(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.sales_by_category(start, end), dates)


@resell_required
def sold_items(request):
    start, end, dates = date_filter(request, default="this_month")
    return render_report(request, reports.sold_items(start, end), dates)


@resell_required
def sourcing(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.sourcing(start, end), dates)


@resell_required
def sell_through(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.sell_through(start, end), dates)


@resell_required
def days_to_sell(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.days_to_sell(start, end), dates)


@resell_required
def returns(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.returns(start, end), dates)


@resell_required
def top_bottom(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.top_bottom(start, end), dates)


# ---------------------------------------------------------------- inventory

@resell_required
def inventory(request):
    return render_report(request, reports.inventory_on_hand())


@resell_required
def aged_inventory(request):
    return render_report(request, reports.aged_inventory())


@resell_required
def unlisted(request):
    return render_report(request, reports.unlisted_backlog())


@resell_required
def locations(request):
    return render_report(request, reports.locations())


@resell_required
def cogs(request):
    year, years = year_filter(request)
    return render_report(request, reports.cogs(year), years)


# ---------------------------------------------------------------- purchases & expenses

@resell_required
def purchases(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.purchases(start, end), dates)


@resell_required
def expenses(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.expenses_report(start, end), dates)


@resell_required
def platform_fees(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.platform_fees(start, end), dates)


@resell_required
def shipping(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.shipping(start, end), dates)


@resell_required
def mileage(request):
    start, end, dates = date_filter(request)
    return render_report(request, reports.mileage(start, end), dates)


# ---------------------------------------------------------------- accounting & tax

@resell_required
def profit_loss(request):
    start, end, dates = date_filter(request)
    group, groups = choice_filter(request, "by", STATEMENT_GROUPS, "month", "Columns")
    return render_report(request, reports.profit_loss(start, end, group), dates, groups)


@resell_required
def cash_flow(request):
    start, end, dates = date_filter(request)
    group, groups = choice_filter(request, "by", STATEMENT_GROUPS, "month", "Show by")
    return render_report(request, reports.cash_flow(start, end, group), dates, groups)


@resell_required
def sales_tax(request):
    start, end, dates = date_filter(request, default="this_quarter")
    return render_report(request, reports.sales_tax(start, end), dates)


@resell_required
def estimated_tax(request):
    year, years = year_filter(request)
    try:
        rate = Decimal(request.GET.get("rate") or DEFAULT_TAX_RATE).quantize(Decimal("0.1"))
    except InvalidOperation:
        rate = DEFAULT_TAX_RATE
    rate = min(max(rate, Decimal("0")), Decimal("50")).normalize()
    rate_box = {"kind": "number", "label": "Your income tax rate (%)", "name": "rate", "value": rate,
                "help": "Your federal tax bracket, e.g. 10, 12 or 22. Not sure? 12% is common.",
                "keep": [(k, v) for k, v in request.GET.items() if k not in ("rate", "format")]}
    return render_report(request, reports.estimated_tax(year, rate), years, rate_box)


@resell_required
def schedule_c(request):
    year, years = year_filter(request)
    return render_report(request, reports.schedule_c(year), years)
