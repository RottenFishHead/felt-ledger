from decimal import Decimal, InvalidOperation
from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def money(value, signed=False):
    """$1,234.50 and −$50.00 (a real minus sign before the dollar sign). `money:"signed"` adds + to gains."""
    if value is None or value == "":
        return "—"
    try:
        value = Decimal(value)
    except (InvalidOperation, TypeError):
        return value
    text = f"${abs(value):,.2f}"
    if value < 0:
        return f"−{text}"
    return f"+{text}" if signed and value > 0 else text


def nice_date(day):
    """Oct 26, 2026"""
    return f"{day:%b} {day.day}, {day.year}"


@register.filter
def tone(value):
    """CSS class for a gain or loss."""
    if value is None:
        return ""
    return "gain" if value > 0 else "loss" if value < 0 else ""


def cell_parts(value, kind):
    """(text, css class) for one report value. "" is a deliberately blank cell; None is a missing number (—)."""
    if isinstance(value, str) and value == "":
        return "", ""
    if value is None:
        return "—", "muted"
    if kind == "money":
        return money(value), ""
    if kind == "signed":
        return money(value, True), tone(value)
    if kind == "pct":
        return f"{value}%", ""
    if kind == "int":
        return f"{value:,}", ""
    if kind == "days":
        return f"{Decimal(value).normalize():f}", ""
    if kind == "miles":
        return f"{value:,.1f}", ""
    if kind == "date":
        return nice_date(value), ""
    return str(value), ""


@register.filter
def cell(value, kind):
    """A report value on screen: formatted for its column kind, with links (items, receipts) clickable."""
    if hasattr(value, "url"):
        return format_html('<a class="link" href="{}">{}</a>', value.url, value.text)
    text, css = cell_parts(value, kind)
    return format_html('<span class="{}">{}</span>', css, text) if css else text


@register.filter
def cell_text(value, kind):
    """A report value in the PDF: plain text."""
    return value.text if hasattr(value, "url") else cell_parts(value, kind)[0]


@register.simple_tag
def report_menu():
    """The Reports dropdown: [(group, [(url name, title, description)])]."""
    from ..reports import CATALOG
    return CATALOG
