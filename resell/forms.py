from django import forms
from django.core.validators import MinValueValidator
from django.db import transaction
from django.utils import timezone
from . import locations
from .models import (Brand, Category, Expense, InventorySettings, Item, ItemImage, Listing, Platform, Source, SubCategory,
                     default_mileage_rate)
from .templatetags.resell_extras import nice_date


class CategoryPicker(forms.CheckboxSelectMultiple):
    """Item types as a dropdown. Main types are fold-out headings; only their sub-types can be ticked."""
    template_name = "resell/widgets/category_picker.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        options = {str(option["value"]): option
                   for _, group, _ in context["widget"]["optgroups"] for option in group}
        tree = []
        for main in Category.objects.prefetch_related("subcategories"):
            subs = [{"option": options[str(sub.pk)], "name": sub.name, "full": f"{main.name} › {sub.name}"}
                    for sub in main.subcategories.all() if str(sub.pk) in options]
            tree.append({"name": main.name, "subs": subs,
                         "ticked": sum(1 for sub in subs if sub["option"]["selected"])})
        context["widget"]["tree"] = tree
        context["widget"]["picked"] = [sub["full"] for group in tree for sub in group["subs"] if sub["option"]["selected"]]
        return context


class ItemForm(forms.ModelForm):
    """Every field at once — used by the Django admin."""
    recalculate_fees = forms.BooleanField(
        required=False,
        label="Recalculate platform fees",
        help_text="Replace the fees with the platform's percentage and flat fee for the current sale price and shipping.",
    )

    class Meta:
        model = Item
        fields = "__all__"

    def save(self, commit=True):
        if self.cleaned_data.get("recalculate_fees"):
            self.instance.recalculate_fees()
        return super().save(commit)


# ---------------------------------------------------------------- widgets

def number_input(placeholder=""):
    """A plain text box that opens the number keypad on phones. Unlike type="number", it has no arrows and the
    mouse wheel can't change the amount. The form field still checks that it's a valid, non-negative number."""
    attrs = {"inputmode": "decimal", "autocomplete": "off"}
    if placeholder:
        attrs["placeholder"] = placeholder
    return forms.TextInput(attrs=attrs)


def money_input(placeholder="0.00"):
    return number_input(placeholder)


def date_input():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """Lets one file box accept several photos at once."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*", "multiple": True}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data if d]
        return [single_clean(data, initial)] if data else []


class BlankMeansZeroMixin:
    """Money boxes listed in `zero_if_blank` may be left empty; empty means $0."""
    zero_if_blank = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.zero_if_blank:
            if name in self.fields:
                self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        for name in self.zero_if_blank:
            if name in self.fields and cleaned.get(name) is None and name not in self.errors:
                cleaned[name] = 0
        return cleaned


def add_photos(item, photos):
    next_order = (item.images.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0) + 1
    for offset, photo in enumerate(photos):
        ItemImage.objects.create(item=item, image=photo, sort_order=next_order + offset)


# ---------------------------------------------------------------- items

class ItemDetailsForm(forms.ModelForm):
    """What the item is and what it cost. Selling, returns and write-offs have their own forms."""
    brand_name = forms.CharField(
        label="Brand", max_length=120, required=False,
        help_text="Start typing — brands you've used before will pop up. Leave blank if there's no brand.",
        widget=forms.TextInput(attrs={"list": "brand-options", "autocomplete": "off"}),
    )
    new_subcategories = forms.CharField(
        label="Add a new sub-type", max_length=200, required=False,
        help_text="Not in the list above? Type it here, e.g. “Sneakers”. Separate several with commas.",
    )
    new_subcategory_category = forms.CharField(
        label="Main type it goes under", max_length=120, required=False,
        help_text="Pick one that pops up, or type a new one, e.g. “Shoes”.",
        widget=forms.TextInput(attrs={"list": "main-type-options", "autocomplete": "off"}),
    )
    new_source_name = forms.CharField(
        label="…or type a new place", max_length=120, required=False,
        help_text="First time buying there? Type the store's name, e.g. “Goodwill on Main St”. "
                  "You can add its address later under Home → Places I buy.",
    )
    photos = MultipleImageField(
        label="Photos",
        help_text="On a phone this opens your camera or photo library. You can pick several photos at once.",
    )
    # The slot the form suggested when it opened. If that one fills up before Save, the next open slot is used.
    offered_location = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Item
        fields = ["title", "subcategories", "condition", "size", "color", "purchase_date", "purchase_price",
                  "source", "storage_location", "sku", "description", "notes"]
        labels = {
            "title": "What is it?",
            "subcategories": "What type of item is it?",
            "condition": "Condition",
            "purchase_date": "Date you bought it",
            "purchase_price": "How much did you pay? ($)",
            "source": "Where did you buy it?",
            "storage_location": "Bin location",
            "sku": "SKU",
            "description": "Description (optional)",
            "notes": "Private notes (optional)",
        }
        help_texts = {
            "title": "A short name you'll recognise, e.g. “Nike Air Max 90, white”.",
            "subcategories": "Open the list, tap a main type, then tick the sub-type. Most items only need one.",
            "size": "Leave blank if it doesn't have a size.",
            "color": "The main color, e.g. “Black”. Leave blank if unsure.",
            "storage_location": "Filled in with the next open slot. Change it if the item doesn't fit there, e.g. “B03”.",
            "sku": "Made from the bin location and the next item number when you save. It never changes.",
            "description": "Notes for your listings: measurements, flaws, materials.",
            "notes": "Only you see these.",
        }
        widgets = {
            "subcategories": CategoryPicker,
            "condition": forms.RadioSelect,
            "purchase_date": date_input(),
            "purchase_price": money_input(),
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].empty_label = "— Pick a place —"
        self.fields["source"].required = False
        self.fields["subcategories"].required = False
        if self.instance.pk and self.instance.brand:
            self.initial["brand_name"] = self.instance.brand.name
        self.brand_options = Brand.objects.values_list("name", flat=True)
        self.main_type_options = Category.objects.values_list("name", flat=True)
        self.location_changed_to = None  # set on save when the chosen slot was taken meanwhile
        item = self.instance
        # New items and items with an item number use bin slots and an automatic SKU; older items keep free text.
        self.uses_slots = not item.pk or item.item_number is not None
        location, sku = self.fields["storage_location"], self.fields["sku"]
        location.widget.attrs.update({"autocomplete": "off", "autocapitalize": "characters", "data-location": ""})
        if item.pk:
            location.widget.attrs["data-item"] = item.pk
        if not item.pk:
            config = InventorySettings.load()
            self.initial.setdefault("storage_location", locations.first_open_slot(config, locations.occupied_slots()))
            self.initial["offered_location"] = self.initial["storage_location"]
            self.initial["sku"] = locations.preview(self.initial["storage_location"], config)
            sku.widget.attrs.update({"data-sku-preview": "", "data-item-number": config.next_item_number})
        if self.uses_slots:
            sku.disabled = True
        else:
            sku.label = "Your item code (optional)"
            sku.help_text = "Only if you label your items with a number. Must be different for every item."
            location.label = "Where is it stored?"
            location.help_text = "So you can find it later, e.g. “A16”, “Bin 4” or “Closet shelf”."
        if item.location_released_at:
            location.disabled = True
            location.help_text = "This item has left its slot (shipped or written off). Kept here as a record."

    def clean_sku(self):
        if self.fields["sku"].disabled:
            return self.instance.sku if self.instance.pk else None
        return (self.cleaned_data["sku"] or "").strip() or None

    def clean_storage_location(self):
        value = (self.cleaned_data["storage_location"] or "").strip()
        item = self.instance
        if self.fields["storage_location"].disabled:
            return item.storage_location
        if not value:
            if item.pk and item.item_number is not None:
                raise forms.ValidationError("Enter the bin slot it's in, e.g. “A16”.")
            return value  # a new item with no location gets the next open slot on save
        if not locations.parse_slot(value):
            if self.uses_slots:
                raise forms.ValidationError("Use a bin letter and slot number, e.g. “A16”.")
            return value
        value = locations.normalize(value)
        if item.pk and value == locations.normalize(item.storage_location):
            return value  # it's already there
        occupied = locations.occupied_slots(exclude_pk=item.pk)
        if not item.pk and value == self.data.get(self.add_prefix("offered_location")) and value in occupied:
            return value  # it was open when the form opened; save picks the next open slot and says so
        problem = locations.slot_problem(value, InventorySettings.load(), occupied)
        if problem:
            raise forms.ValidationError(problem)
        return value

    def clean(self):
        cleaned = super().clean()
        new_source = cleaned.get("new_source_name", "").strip()
        if new_source and cleaned.get("source"):
            self.add_error("new_source_name", "Pick a place from the list or type a new one — not both.")
        new_subs = cleaned.get("new_subcategories", "").strip()
        main = cleaned.get("new_subcategory_category", "").strip()
        if new_subs and not main:
            self.add_error("new_subcategory_category", "Every sub-type goes under a main type. Pick or type one.")
        elif main and not new_subs:
            self.add_error("new_subcategories", "Type the new sub-type, or clear the main type box.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        new = not self.instance.pk
        old_location = self.initial.get("storage_location", "")
        item = super().save(commit=False)
        brand_name = self.cleaned_data["brand_name"].strip()
        item.brand = get_or_create_named(Brand, brand_name) if brand_name else None
        new_source = self.cleaned_data["new_source_name"].strip()
        if new_source:
            item.source = get_or_create_named(Source, new_source)
        if new:
            requested = self.cleaned_data["storage_location"]
            used = locations.save_new_item(item, requested)
            if requested and used != requested:
                self.location_changed_to = used
        else:
            if item.storage_location != old_location and not item.location_released_at:
                new_location, item.storage_location = item.storage_location, old_location
                locations.move(item, new_location)
            item.save()
        self.save_m2m()
        names = [name.strip() for name in self.cleaned_data["new_subcategories"].split(",") if name.strip()]
        if names:
            main = get_or_create_named(Category, self.cleaned_data["new_subcategory_category"].strip())
            item.subcategories.add(*[get_or_create_subcategory(main, name) for name in names])
        add_photos(item, self.cleaned_data["photos"])
        return item


def get_or_create_named(model, name):
    """Find an existing brand/type/place ignoring capital letters, or create it."""
    existing = model.objects.filter(name__iexact=name).first()
    return existing or model.objects.create(name=name)


def get_or_create_subcategory(category, name):
    """Find the sub-type with this name in this main type (ignoring capital letters), or create it."""
    existing = category.subcategories.filter(name__iexact=name).first()
    return existing or SubCategory.objects.create(category=category, name=name)


class PhotoForm(forms.Form):
    photos = MultipleImageField(label="Add photos", required=True,
                                help_text="You can pick several photos at once.")


class SaleForm(BlankMeansZeroMixin, forms.ModelForm):
    zero_if_blank = ("shipping_charged", "shipping_cost", "other_sale_costs")
    recalculate_fees = forms.BooleanField(
        required=False, label="Work out the fees again for me",
        help_text="Tick this if you changed the price or site and want the fees estimated again.",
    )

    class Meta:
        model = Item
        fields = ["sale_date", "platform", "sale_price", "shipping_charged", "shipping_cost",
                  "platform_fees", "other_sale_costs"]
        labels = {
            "sale_date": "Date it sold",
            "platform": "Where did it sell?",
            "sale_price": "Sale price ($)",
            "shipping_charged": "Shipping the buyer paid ($)",
            "shipping_cost": "What you paid for the shipping label ($)",
            "platform_fees": "Fees the site charged ($)",
            "other_sale_costs": "Other selling costs ($)",
        }
        help_texts = {
            "platform": "The site or app the buyer used. “In person” is for cash or local pick-up sales.",
            "sale_price": "The item price only, not counting shipping or tax.",
            "shipping_charged": "Leave as 0 if shipping was free or it was picked up.",
            "shipping_cost": "Leave as 0 if the buyer's label was prepaid (like Poshmark) or it was picked up.",
            "platform_fees": "Best: copy the exact fees from the site's sale or order page. "
                             "Leave blank and we'll estimate them from the site's fee settings.",
            "other_sale_costs": "Boxes, mailers, promoted-listing fees. Leave as 0 if none.",
        }
        widgets = {
            "sale_date": date_input(),
            "platform": forms.RadioSelect,
            "sale_price": money_input(),
            "shipping_charged": money_input(),
            "shipping_cost": money_input(),
            "platform_fees": money_input("Leave blank to estimate"),
            "other_sale_costs": money_input(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("sale_date", "platform", "sale_price"):
            self.fields[name].required = True
        self.fields["platform"].empty_label = None
        self.editing_sale = self.instance.has_active_sale
        self.selling_new_after_return = bool(self.instance.return_date and not self.editing_sale)
        if not self.editing_sale:
            # A fresh sale (first one, or after a return): start from today and the current listing, not old values.
            del self.fields["recalculate_fees"]
            self.initial.update(sale_date=timezone.localdate(), platform_fees=None)
            active = list(self.instance.listings.filter(status=Listing.ACTIVE)[:2])
            if len(active) == 1:  # only listed on one site: pre-pick it
                self.initial.update(platform=active[0].platform_id, sale_price=active[0].price)

    def clean(self):
        cleaned = super().clean()
        sale_date = cleaned.get("sale_date")
        if self.instance.written_off_date:
            raise forms.ValidationError("This item is written off. Undo the write-off before recording a sale.")
        if self.selling_new_after_return and sale_date and sale_date <= self.instance.return_date:
            self.add_error("sale_date", f"This item came back on {nice_date(self.instance.return_date)}. "
                                        "The new sale date must be after that.")
        return cleaned

    def save(self, commit=True):
        item = super().save(commit=False)
        if self.cleaned_data.get("recalculate_fees") or self.cleaned_data.get("platform_fees") is None:
            item.platform_fees = None  # Item.save() estimates fees when blank
        if commit:
            item.save()
        return item


class ReturnForm(forms.Form):
    return_date = forms.DateField(label="Date it came back", widget=date_input(),
                                  help_text="The day you refunded the buyer or got the item back.")
    return_cost = forms.DecimalField(
        label="Money you lost on this return ($)", min_value=0, max_digits=11, decimal_places=2, initial=0, required=False,
        widget=money_input(),
        help_text="Return shipping you paid, fees the site kept, restocking. Leave as 0 if you got everything back.",
    )
    return_reason = forms.CharField(label="Why did it come back? (optional)", max_length=255, required=False,
                                    help_text="e.g. “Didn't fit” or “Arrived damaged”.")

    def __init__(self, *args, item, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        self.initial.setdefault("return_date", timezone.localdate())

    def clean_return_date(self):
        value = self.cleaned_data["return_date"]
        if value < self.item.sale_date:
            raise forms.ValidationError(f"It sold on {nice_date(self.item.sale_date)}, so it can't come back before that.")
        return value

    def save(self):
        item = self.item
        item.return_date = self.cleaned_data["return_date"]
        item.return_cost += self.cleaned_data["return_cost"] or 0  # add to any earlier returns of this item
        if self.cleaned_data["return_reason"]:
            item.return_reason = self.cleaned_data["return_reason"]
        self.new_location = None
        with transaction.atomic():
            if item.location_released_at:  # it had shipped, so it needs a slot again
                self.new_location = locations.restock(item)
            item.save()
        return item


class ReturnFixForm(BlankMeansZeroMixin, forms.ModelForm):
    zero_if_blank = ("return_cost",)
    remove_return = forms.BooleanField(
        required=False, label="This item was never returned — remove the return",
        help_text="Use this if you recorded a return by mistake. The sale will count again.",
    )

    class Meta:
        model = Item
        fields = ["return_date", "return_cost", "return_reason"]
        labels = {"return_date": "Date it came back", "return_cost": "Total money lost on returns ($)",
                  "return_reason": "Why it came back"}
        help_texts = {"return_cost": "For all returns of this item added together."}
        widgets = {"return_date": date_input(), "return_cost": money_input()}

    def save(self, commit=True):
        item = super().save(commit=False)
        if self.cleaned_data.get("remove_return"):
            item.return_date, item.return_cost, item.return_reason = None, 0, ""
            if item.shipped_at:  # the sale counts again and the item had shipped, so it's not on the shelf
                locations.release(item)
        if commit:
            item.save()
        return item


class WriteOffForm(forms.Form):
    written_off_date = forms.DateField(label="Date", widget=date_input())
    reason = forms.CharField(label="What happened? (optional)", max_length=200, required=False,
                             help_text="e.g. “Donated to Goodwill” or “Stained, threw it out”.")

    def __init__(self, *args, item, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        self.initial.setdefault("written_off_date", timezone.localdate())

    def clean_written_off_date(self):
        value = self.cleaned_data["written_off_date"]
        if value < self.item.purchase_date:
            raise forms.ValidationError("That's before you bought it.")
        return value

    def save(self):
        item = self.item
        item.written_off_date = self.cleaned_data["written_off_date"]
        if self.cleaned_data["reason"]:
            line = f"Written off {nice_date(item.written_off_date)}: {self.cleaned_data['reason']}"
            item.notes = f"{item.notes}\n{line}".strip()
        locations.release(item)
        item.save()
        return item


# ---------------------------------------------------------------- listings

class ListingForm(forms.ModelForm):
    class Meta:
        model = Listing
        fields = ["platform", "price", "listed_date", "url"]
        labels = {"platform": "Which site?", "price": "Your asking price ($)", "listed_date": "Date you listed it",
                  "url": "Link to the listing (optional)"}
        help_texts = {
            "url": "Open your listing, tap Share → Copy link, then paste it here. Lets you jump straight to it later.",
        }
        widgets = {"platform": forms.RadioSelect, "price": money_input(), "listed_date": date_input()}

    def __init__(self, *args, item, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        self.instance.item = item
        self.fields["platform"].empty_label = None
        self.initial.setdefault("listed_date", timezone.localdate())

    def clean(self):
        cleaned = super().clean()
        platform = cleaned.get("platform")
        if platform and self.instance.status == Listing.ACTIVE:
            clash = self.item.listings.filter(platform=platform, status=Listing.ACTIVE).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("platform", f"It's already listed on {platform}. Edit that listing instead.")
        return cleaned


# ---------------------------------------------------------------- expenses

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["date", "category", "amount", "miles", "mileage_rate", "description", "receipt"]
        labels = {"category": "What kind of expense?", "amount": "Amount ($)", "miles": "Miles driven",
                  "mileage_rate": "Rate per mile ($)", "description": "What was it for?",
                  "receipt": "Receipt photo (optional)"}
        help_texts = {
            "amount": "The total on the receipt.",
            "miles": "Round trip, for business only. Your phone's map app can tell you the distance.",
            "mileage_rate": "Already filled in with the standard rate. Only change it if you know it's different.",
            "description": "e.g. “Poly mailers” or “Drove to estate sale on Elm St”.",
            "receipt": "Take a photo of the paper receipt, or attach a PDF. Handy at tax time.",
        }
        widgets = {
            "date": date_input(),
            "category": forms.RadioSelect,
            "amount": money_input(),
            "miles": number_input(),
            "mileage_rate": number_input(),
            "receipt": forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].required = False
        self.fields["mileage_rate"].validators.append(MinValueValidator(0))
        if not self.instance.mileage_rate:
            self.initial.setdefault("mileage_rate", default_mileage_rate())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("amount") is None:
            cleaned["amount"] = 0
        if cleaned.get("category") != Expense.MILEAGE:
            cleaned["miles"] = cleaned["mileage_rate"] = None
        return cleaned


# ---------------------------------------------------------------- item types, places & selling sites

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]
        labels = {"name": "Name of the main type"}
        help_texts = {"name": "e.g. “Shoes”, “Handbags” or “Home decor”."}


class SubCategoryForm(forms.ModelForm):
    class Meta:
        model = SubCategory
        fields = ["category", "name"]
        labels = {"category": "Main type it goes under", "name": "Name of the sub-type"}
        help_texts = {"category": "Moving a sub-type moves it on every item that has it.",
                      "name": "e.g. “Sneakers” or “Boots”."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].empty_label = "— Pick a main type —"


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = ["name", "kind", "street_address", "address_line_2", "city", "state", "postal_code"]
        labels = {"name": "Name of the place", "kind": "What kind of place?", "address_line_2": "Suite / unit",
                  "postal_code": "ZIP code"}
        help_texts = {"name": "e.g. “Goodwill on Main St”.",
                      "street_address": "Leave the address blank for online shops. Fill it in to see a map."}
        widgets = {"kind": forms.RadioSelect}


class PlatformForm(forms.ModelForm):
    class Meta:
        model = Platform
        fields = ["name", "fee_percent", "fee_fixed", "collects_sales_tax"]
        labels = {"name": "Site name", "fee_percent": "Fee percentage (%)", "fee_fixed": "Flat fee per sale ($)",
                  "collects_sales_tax": "This site collects sales tax for me"}
        help_texts = {
            "name": "e.g. “eBay”, “Poshmark”, “Facebook Marketplace” or “In person”.",
            "fee_percent": "The share of each sale the site keeps, e.g. 13.25. Use 0 for in-person sales.",
            "fee_fixed": "A fixed charge per order, e.g. 0.40. Use 0 if there isn't one.",
            "collects_sales_tax": "Most marketplaces (eBay, Poshmark, Mercari, Etsy…) do. Untick for in-person or "
                                  "direct sales, where you may need to collect it yourself.",
        }
        widgets = {"fee_percent": number_input(),
                   "fee_fixed": money_input()}
