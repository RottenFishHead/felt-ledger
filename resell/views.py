from datetime import timedelta
from decimal import Decimal
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from . import locations
from .access import resell_required
from .forms import (CategoryForm, ExpenseForm, ItemDetailsForm, ListingForm, PhotoForm, PlatformForm, ReturnFixForm, ReturnForm,
                    SaleForm, SourceForm, SubCategoryForm, WriteOffForm, add_photos)
from .models import Category, Expense, InventorySettings, Item, ItemImage, Listing, Platform, Source, SubCategory

ZERO = Decimal("0.00")

# The filter tabs on "My items": (key, label, statuses shown)
ITEM_TABS = [
    ("all", "All", None),
    ("unlisted", "Not listed yet", Item.NOT_LISTED),
    ("listed", "Listed", (Item.LISTED,)),
    ("sold", "Sold", (Item.SOLD,)),
    ("written_off", "Written off", (Item.WRITTEN_OFF,)),
]


def money_summary(sold_items, expenses):
    sold_items = list(sold_items)
    sales = sum((item.sale_price + item.shipping_charged for item in sold_items), ZERO)
    profit = sum((item.profit for item in sold_items), ZERO)
    spent = expenses.aggregate(total=Sum("amount"))["total"] or ZERO
    return {"count": len(sold_items), "sales": sales, "profit": profit, "expenses": spent, "net": profit - spent}


def confirm_delete(request, obj, *, title, message, cancel_url, success_url, success_message, protected_message=""):
    """Shared "Are you sure?" page for every delete button."""
    cancel_url, success_url = resolve_url(cancel_url), resolve_url(success_url)
    if request.method == "POST":
        try:
            obj.delete()
        except ProtectedError:
            messages.error(request, protected_message)
            return redirect(cancel_url)
        messages.success(request, success_message)
        return redirect(success_url)
    return render(request, "resell/confirm_delete.html",
                  {"title": title, "message": message, "cancel_url": cancel_url})


# ---------------------------------------------------------------- home & help

@resell_required
def home(request):
    today = timezone.localdate()
    sold = Item.objects.filter(status=Item.SOLD)
    month = money_summary(sold.filter(sale_date__year=today.year, sale_date__month=today.month),
                          Expense.objects.filter(date__year=today.year, date__month=today.month))
    year = money_summary(sold.filter(sale_date__year=today.year), Expense.objects.filter(date__year=today.year))
    unsold = Item.objects.filter(status__in=Item.UNSOLD)
    tied_up = unsold.aggregate(price=Sum("purchase_price"))
    context = {
        "today": today,
        "month": month,
        "year": year,
        "not_listed_count": unsold.exclude(status=Item.LISTED).count(),
        "listed_count": unsold.filter(status=Item.LISTED).count(),
        "money_tied_up": tied_up["price"] or ZERO,
        "to_delist": Listing.objects.filter(status=Listing.ACTIVE, item__status__in=[Item.SOLD, Item.WRITTEN_OFF])
                                    .select_related("item", "platform"),
        "sitting": unsold.filter(purchase_date__lte=today - timedelta(days=90)).count(),
        "recent_sales": sold.select_related("platform").order_by("-sale_date", "-pk")[:5],
        "has_platforms": Platform.objects.exists(),
    }
    return render(request, "resell/home.html", context)


@resell_required
def help_page(request):
    return render(request, "resell/help.html")


# ---------------------------------------------------------------- items

@resell_required
def item_list(request):
    # ?action=sell / ?action=list: "pick the item" mode used by the big buttons on the home page.
    action = request.GET.get("action") if request.GET.get("action") in ("sell", "list") else ""
    tab = "unsold" if action else request.GET.get("show", "all")
    statuses = Item.UNSOLD if action else dict((key, statuses) for key, _, statuses in ITEM_TABS).get(tab)
    items = Item.objects.select_related("brand", "platform").prefetch_related("images", "listings__platform")
    if statuses:
        items = items.filter(status__in=statuses)
    if tab == "sitting":
        items = items.filter(status__in=Item.UNSOLD, purchase_date__lte=timezone.localdate() - timedelta(days=90))
    search = request.GET.get("q", "").strip()
    if search:
        items = items.filter(Q(title__icontains=search) | Q(brand__name__icontains=search) | Q(sku__iexact=search)
                             | Q(storage_location__icontains=search) | Q(description__icontains=search))
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    return render(request, "resell/item_list.html",
                  {"page": page, "tabs": ITEM_TABS, "tab": tab, "search": search, "action": action})


@resell_required
def item_create(request):
    form = ItemDetailsForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        if form.location_changed_to:
            messages.warning(request, f"{form.cleaned_data['storage_location']} was just filled (maybe on another device), "
                                      f"so this item went in {item.storage_location} instead. Its SKU is {item.sku}.")
        messages.success(request, f"Saved “{item}” as {item.sku} — put it in {item.storage_location}. "
                                  "Next step: when you list it for sale, tap “I listed it”.")
        return redirect("resell:item-detail", pk=item.pk)
    return render(request, "resell/item_form.html", {"form": form})


def item_pk_param(request):
    """The ?item= the slot checks leave out (the item being edited), or None."""
    value = request.GET.get("item", "")
    return int(value) if value.isdigit() else None


@resell_required
def location_check(request):
    """For the add-item form: is this slot open, and what SKU would it give? Reserves nothing."""
    location = locations.normalize(request.GET.get("location", ""))
    item_pk = item_pk_param(request)
    config = InventorySettings.load()
    occupied = locations.occupied_slots(exclude_pk=item_pk)
    current = item_pk and Item.objects.filter(pk=item_pk).values_list("storage_location", flat=True).first()
    error = None if current and locations.normalize(current) == location else locations.slot_problem(location, config, occupied)
    return JsonResponse({"location": location, "sku": locations.preview(location, config), "error": error})


@resell_required
def location_next_bin(request):
    """For the "Bin full → next bin" button: the first open slot in the bin after this one."""
    location = locations.next_bin_location(request.GET.get("after", ""), exclude_pk=item_pk_param(request))
    return JsonResponse({"location": location, "sku": locations.preview(location)})


@resell_required
def item_edit(request, pk):
    item = get_object_or_404(Item, pk=pk)
    form = ItemDetailsForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your changes are saved.")
        return redirect("resell:item-detail", pk=item.pk)
    return render(request, "resell/item_form.html", {"form": form, "item": item})


@resell_required
def item_detail(request, pk):
    item = get_object_or_404(Item.objects.select_related("brand", "source", "platform"), pk=pk)
    return render(request, "resell/item_detail.html", {
        "item": item,
        "listings": item.listings.select_related("platform"),
        "photos": item.images.all(),
        "photo_form": PhotoForm(),
        "can_list": item.status in Item.UNSOLD,
    })


@resell_required
def item_delete(request, pk):
    item = get_object_or_404(Item, pk=pk)
    return confirm_delete(
        request, item,
        title=f"Delete “{item}”?",
        message="This removes the item, its photos and its listings for good. Its sale will no longer count in "
                "your totals. If you sold it or gave it away, use “It sold” or “Get rid of it” instead.",
        cancel_url=item.get_absolute_url(), success_url="resell:item-list",
        success_message=f"Deleted “{item}”.",
    )


# ---------------------------------------------------------------- selling, returns, write-offs

@resell_required
def item_sell(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.written_off_date:
        messages.error(request, "This item is written off. Tap “Undo write-off” first if you sold it after all.")
        return redirect(item)
    if not Platform.objects.exists():
        messages.error(request, "First add the sites you sell on (eBay, Poshmark…). It only takes a minute.")
        return redirect("resell:platform-create")
    form = SaleForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        editing = form.editing_sale
        item = form.save()
        profit = item.profit
        verdict = f"you made ${profit:,.2f}" if profit >= 0 else f"you lost ${-profit:,.2f}"
        messages.success(request, ("Sale updated" if editing else "Congratulations on the sale") + f" — {verdict}.")
        return redirect(item)
    return render(request, "resell/sale_form.html", {"form": form, "item": item})


@resell_required
@require_POST
def item_sale_undo(request, pk):
    item = get_object_or_404(Item, pk=pk)
    sold_listing = item.listings.filter(status=Listing.SOLD, platform=item.platform).first()
    item.sale_date = item.sale_price = item.platform = item.platform_fees = None
    item.shipping_charged = item.shipping_cost = item.other_sale_costs = ZERO
    item.shipped_at = None
    with transaction.atomic():
        new_location = locations.restock(item, keep_slot_if_open=True) if item.location_released_at else None
        item.save()
    if sold_listing:
        sold_listing.status, sold_listing.ended_date = Listing.ACTIVE, None
        sold_listing.save()
    messages.success(request, "The sale was removed. The item is back in your stock."
                     + (f" Put it in {new_location}." if new_location else ""))
    return redirect(item)


@resell_required
@require_POST
def item_ship(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.status != Item.SOLD:
        messages.error(request, "Only sold items can be shipped.")
        return redirect(item)
    item.shipped_at = timezone.now()
    locations.release(item)
    item.save()
    freed = f" {item.storage_location} is free for the next item." if locations.parse_slot(item.storage_location) else ""
    messages.success(request, "Marked as shipped." + freed)
    return redirect(item)


@resell_required
@require_POST
def item_ship_undo(request, pk):
    item = get_object_or_404(Item, pk=pk)
    item.shipped_at = None
    with transaction.atomic():
        new_location = locations.restock(item, keep_slot_if_open=True) if item.location_released_at else None
        item.save()
    messages.success(request, "Not shipped after all." + (f" It's in {new_location}." if new_location else ""))
    return redirect(item)


@resell_required
def item_return(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if not item.has_active_sale:
        messages.error(request, "Only sold items can be returned.")
        return redirect(item)
    form = ReturnForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        if form.new_location:
            messages.warning(request, f"Shelve it in {form.new_location} and re-tag it there. Its SKU stays {item.sku or 'the same'}.")
        messages.success(request, "Return recorded. The item is back in your stock — list it again when you're ready.")
        return redirect(item)
    return render(request, "resell/return_form.html", {"form": form, "item": item})


@resell_required
def item_return_fix(request, pk):
    item = get_object_or_404(Item, pk=pk)
    form = ReturnFixForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Return details updated.")
        return redirect(item)
    return render(request, "resell/return_fix_form.html", {"form": form, "item": item})


@resell_required
def item_write_off(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.has_active_sale:
        messages.error(request, "This item is sold. Remove the sale first if it didn't really sell.")
        return redirect(item)
    form = WriteOffForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Written off. Its cost now counts as a loss in your totals.")
        return redirect(item)
    return render(request, "resell/write_off_form.html", {"form": form, "item": item})


@resell_required
@require_POST
def item_write_off_undo(request, pk):
    item = get_object_or_404(Item, pk=pk)
    item.written_off_date = None
    with transaction.atomic():
        new_location = locations.restock(item, keep_slot_if_open=True) if item.location_released_at else None
        item.save()
    messages.success(request, "Write-off undone. The item is back in your stock."
                     + (f" Put it in {new_location}." if new_location else ""))
    return redirect(item)


# ---------------------------------------------------------------- listings

@resell_required
def listing_create(request, item_pk):
    item = get_object_or_404(Item, pk=item_pk)
    if item.status not in Item.UNSOLD:
        messages.error(request, "This item is sold or written off, so it can't be listed.")
        return redirect(item)
    if not Platform.objects.exists():
        messages.error(request, "First add the sites you sell on (eBay, Poshmark…). It only takes a minute.")
        return redirect("resell:platform-create")
    form = ListingForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        listing = form.save()
        messages.success(request, f"Listed on {listing.platform}. Listed it somewhere else too? Add that site the same way.")
        return redirect(item)
    return render(request, "resell/listing_form.html", {"form": form, "item": item})


@resell_required
def listing_edit(request, pk):
    listing = get_object_or_404(Listing.objects.select_related("item"), pk=pk)
    form = ListingForm(request.POST or None, instance=listing, item=listing.item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Listing updated.")
        return redirect(listing.item)
    return render(request, "resell/listing_form.html", {"form": form, "item": listing.item, "listing": listing})


@resell_required
@require_POST
def listing_end(request, pk):
    listing = get_object_or_404(Listing.objects.select_related("item", "platform"), pk=pk)
    listing.status = Listing.ENDED
    listing.save()
    messages.success(request, f"Marked the {listing.platform} listing as taken down.")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = listing.item.get_absolute_url()
    return redirect(next_url)


@resell_required
def listing_delete(request, pk):
    listing = get_object_or_404(Listing.objects.select_related("item", "platform"), pk=pk)
    return confirm_delete(
        request, listing,
        title=f"Remove the {listing.platform} listing?",
        message="Only do this if you added it by mistake. If you took the listing down, use “Taken down” instead "
                "so you keep its history.",
        cancel_url=listing.item.get_absolute_url(), success_url=listing.item.get_absolute_url(),
        success_message="Listing removed.",
    )


# ---------------------------------------------------------------- photos

@resell_required
@require_POST
def photo_add(request, item_pk):
    item = get_object_or_404(Item, pk=item_pk)
    form = PhotoForm(request.POST, request.FILES)
    if form.is_valid():
        add_photos(item, form.cleaned_data["photos"])
        messages.success(request, "Photos added.")
    else:
        messages.error(request, " ".join(form.errors.get("photos", ["Those photos couldn't be added."])))
    return redirect(item)


@resell_required
@require_POST
def photo_cover(request, pk):
    photo = get_object_or_404(ItemImage, pk=pk)
    others = photo.item.images.exclude(pk=photo.pk)
    for order, image in enumerate([photo, *others], start=1):
        ItemImage.objects.filter(pk=image.pk).update(sort_order=order)
    messages.success(request, "That photo is now the main photo.")
    return redirect(photo.item)


@resell_required
@require_POST
def photo_delete(request, pk):
    photo = get_object_or_404(ItemImage, pk=pk)
    item = photo.item
    photo.delete()
    messages.success(request, "Photo deleted.")
    return redirect(item)


# ---------------------------------------------------------------- expenses

@resell_required
def expense_list(request):
    today = timezone.localdate()
    years = sorted({d.year for d in Expense.objects.dates("date", "year")} | {today.year}, reverse=True)
    picked = request.GET.get("year", "")
    year = int(picked) if picked.isdigit() and int(picked) in years else today.year
    expenses = Expense.objects.filter(date__year=year)
    by_category = {label: ZERO for _, label in Expense.CATEGORY_CHOICES}
    for expense in expenses:
        by_category[expense.get_category_display()] += expense.amount
    return render(request, "resell/expense_list.html", {
        "expenses": expenses, "year": year, "years": years,
        "total": sum(by_category.values(), ZERO),
        "by_category": [(label, amount) for label, amount in by_category.items() if amount],
        "miles": sum((e.miles for e in expenses if e.miles), Decimal("0")),
    })


@resell_required
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save()
        messages.success(request, f"Saved a ${expense.amount:,.2f} expense.")
        return redirect("resell:expense-list")
    return render(request, "resell/expense_form.html", {"form": form})


@resell_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expense updated.")
        return redirect("resell:expense-list")
    return render(request, "resell/expense_form.html", {"form": form, "expense": expense})


@resell_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    return confirm_delete(
        request, expense, title="Delete this expense?",
        message=f"{expense} will be removed, along with its receipt photo.",
        cancel_url="resell:expense-list", success_url="resell:expense-list", success_message="Expense deleted.",
    )


# ---------------------------------------------------------------- item types (main types and their sub-types)

@resell_required
def category_list(request):
    categories = Category.objects.prefetch_related("subcategories").annotate(item_count=Count("subcategories__items"))
    return render(request, "resell/category_list.html", {"categories": categories})


@resell_required
def category_create(request):
    return type_form(request, CategoryForm(request.POST or None))


@resell_required
def category_edit(request, pk):
    return type_form(request, CategoryForm(request.POST or None, instance=get_object_or_404(Category, pk=pk)))


@resell_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    return confirm_delete(
        request, category, title=f"Delete “{category}”?", message="This main type has no sub-types, so no items use it.",
        cancel_url="resell:category-list", success_url="resell:category-list", success_message="Main type deleted.",
        protected_message=f"“{category}” still has sub-types. Move or delete them first.",
    )


@resell_required
def subcategory_create(request, category_pk):
    category = get_object_or_404(Category, pk=category_pk)
    return type_form(request, SubCategoryForm(request.POST or None, instance=SubCategory(category=category)))


@resell_required
def subcategory_edit(request, pk):
    return type_form(request, SubCategoryForm(request.POST or None, instance=get_object_or_404(SubCategory, pk=pk)))


@resell_required
def subcategory_delete(request, pk):
    subcategory = get_object_or_404(SubCategory, pk=pk)
    return confirm_delete(
        request, subcategory, title=f"Delete “{subcategory}”?",
        message="Items of this type are kept; they just won't have this type any more.",
        cancel_url="resell:category-list", success_url="resell:category-list", success_message="Sub-type deleted.",
    )


def type_form(request, form):
    """Add/edit page shared by main types and sub-types."""
    return edit_page(request, form, "resell/category_form.html", "resell:category-list", obj=form.instance)


def edit_page(request, form, template, done_url, **context):
    """Add/edit page for item types, places and selling sites: save, then go back to the list."""
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Saved “{saved}”.")
        return redirect(done_url)
    return render(request, template, {"form": form, **context})


# ---------------------------------------------------------------- places I buy

@resell_required
def source_list(request):
    return render(request, "resell/source_list.html", {"sources": Source.objects.all()})


@resell_required
def source_create(request):
    return source_form(request, Source())


@resell_required
def source_edit(request, pk):
    return source_form(request, get_object_or_404(Source, pk=pk))


def source_form(request, source):
    form = SourceForm(request.POST or None, instance=source)
    return edit_page(request, form, "resell/source_form.html", "resell:source-list", source=source)


@resell_required
def source_delete(request, pk):
    source = get_object_or_404(Source, pk=pk)
    return confirm_delete(
        request, source, title=f"Delete “{source}”?",
        message="Items you bought there are kept; they just won't show where they came from.",
        cancel_url="resell:source-list", success_url="resell:source-list", success_message="Place deleted.",
    )


# ---------------------------------------------------------------- sites I sell on

@resell_required
def platform_list(request):
    return render(request, "resell/platform_list.html", {"platforms": Platform.objects.all()})


@resell_required
def platform_create(request):
    return platform_form(request, Platform())


@resell_required
def platform_edit(request, pk):
    return platform_form(request, get_object_or_404(Platform, pk=pk))


def platform_form(request, platform):
    form = PlatformForm(request.POST or None, instance=platform)
    return edit_page(request, form, "resell/platform_form.html", "resell:platform-list", platform=platform)


@resell_required
def platform_delete(request, pk):
    platform = get_object_or_404(Platform, pk=pk)
    return confirm_delete(
        request, platform, title=f"Delete “{platform}”?",
        message="You can only delete a site you haven't listed or sold anything on.",
        cancel_url="resell:platform-list", success_url="resell:platform-list", success_message="Site deleted.",
        protected_message=f"“{platform}” can't be deleted because items were listed or sold there. "
                          "That history is needed for your totals.",
    )
