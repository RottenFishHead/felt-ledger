import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.text import get_valid_filename
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps
from pokertracker.addresses import AddressFields

MONEY = {"max_digits": 11, "decimal_places": 2}
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class Brand(models.Model):
    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = [Lower("name")]

    def __str__(self):
        return self.name


class Category(models.Model):
    """A main type, e.g. Shoes. It only groups sub-types; items are given a SubCategory."""
    name = models.CharField(max_length=120)

    class Meta:
        ordering = [Lower("name")]
        verbose_name, verbose_name_plural = "main type", "main types"
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_category_name",
                                    violation_error_message="There's already a main type with this name."),
        ]

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    """A sub-type inside a main type, e.g. Sneakers in Shoes. Items are only ever given sub-types."""
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="subcategories",
                                 verbose_name="main type")
    name = models.CharField(max_length=120)

    class Meta:
        ordering = [Lower("category__name"), Lower("name")]
        verbose_name, verbose_name_plural = "sub-type", "sub-types"
        constraints = [
            models.UniqueConstraint(Lower("name"), "category", name="unique_subcategory_per_category",
                                    violation_error_message="That main type already has a sub-type with this name."),
        ]

    def __str__(self):
        return f"{self.category.name} › {self.name}"


class Source(AddressFields):
    THRIFT = "thrift"
    RETAIL = "retail"
    GARAGE_SALE = "garage_sale"
    ESTATE_SALE = "estate_sale"
    ONLINE = "online"
    WHOLESALE = "wholesale"
    OTHER = "other"
    KIND_CHOICES = [
        (THRIFT, "Thrift store"), (RETAIL, "Retail / clearance"), (GARAGE_SALE, "Garage sale"),
        (ESTATE_SALE, "Estate sale"), (ONLINE, "Online"), (WHOLESALE, "Wholesale / liquidation"), (OTHER, "Other"),
    ]

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=THRIFT)

    class Meta:
        ordering = [Lower("name")]

    def __str__(self):
        return self.name


class Platform(models.Model):
    name = models.CharField(max_length=80, unique=True)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)],
                                      help_text="Percentage of the sale total the platform keeps, e.g. 13.25.")
    fee_fixed = models.DecimalField(**MONEY, default=ZERO, validators=[MinValueValidator(ZERO)],
                                    help_text="Flat fee charged per order.")
    collects_sales_tax = models.BooleanField(default=True,
                                             help_text="The marketplace collects and pays sales tax on your sales.")

    class Meta:
        ordering = [Lower("name")]

    def __str__(self):
        return self.name

    def estimate_fees(self, sale_total):
        return (sale_total * self.fee_percent / 100 + self.fee_fixed).quantize(CENT, rounding=ROUND_HALF_UP)


class Item(models.Model):
    IN_INVENTORY = "inventory"
    LISTED = "listed"
    SOLD = "sold"
    RETURNED = "returned"
    WRITTEN_OFF = "written_off"
    STATUS_CHOICES = [
        (IN_INVENTORY, "In inventory"), (LISTED, "Listed"), (SOLD, "Sold"),
        (RETURNED, "Returned"), (WRITTEN_OFF, "Donated / written off"),
    ]
    UNSOLD = (IN_INVENTORY, LISTED, RETURNED)  # still yours to sell
    NOT_LISTED = (IN_INVENTORY, RETURNED)
    CONDITION_CHOICES = [
        ("nwt", "New with tags"), ("nwot", "New without tags"), ("excellent", "Excellent"),
        ("good", "Good"), ("fair", "Fair"), ("parts", "For parts"),
    ]

    # Description
    title = models.CharField(max_length=200)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="items")
    subcategories = models.ManyToManyField(SubCategory, blank=True, related_name="items", verbose_name="types")
    sku = models.CharField("SKU", max_length=60, unique=True, null=True, blank=True)
    # Given once on save from InventorySettings.next_item_number; never reused. Older items have none.
    item_number = models.PositiveIntegerField(unique=True, null=True, blank=True, editable=False)
    size = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=40, blank=True)
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, default="good")
    description = models.TextField(blank=True)

    # Purchase
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="items")
    purchase_date = models.DateField(default=timezone.localdate)
    purchase_price = models.DecimalField(**MONEY, default=ZERO)

    # Inventory. Status is derived from the dates below and the item's listings (see compute_status).
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=IN_INVENTORY, editable=False, db_index=True)
    storage_location = models.CharField(max_length=80, blank=True, help_text="Bin slot, e.g. “A16”.")
    # Set when the item leaves its slot (shipped or written off); the slot is then open again.
    # storage_location is kept as a record of where it was.
    location_released_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    location_history = models.JSONField(default=list, blank=True, editable=False,
                                        help_text="Earlier slots: [{\"location\": \"A16\", \"until\": \"2026-10-26T…\"}].")
    written_off_date = models.DateField(null=True, blank=True,
                                        help_text="Set when the item is donated, thrown out or otherwise written off.")

    # Sale
    sale_date = models.DateField(null=True, blank=True, db_index=True)
    sale_price = models.DecimalField(**MONEY, null=True, blank=True)
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, null=True, blank=True, related_name="items",
                                 help_text="Where the item sold.")
    shipping_charged = models.DecimalField(**MONEY, default=ZERO, help_text="Shipping the buyer paid.")
    shipping_cost = models.DecimalField(**MONEY, default=ZERO, help_text="What you paid for the shipping label.")
    platform_fees = models.DecimalField(**MONEY, null=True, blank=True,
                                        help_text="Leave blank to calculate from the platform's fee settings.")
    other_sale_costs = models.DecimalField(**MONEY, default=ZERO, help_text="Packaging, promoted listings, etc.")

    # Return. A return dated on/after the sale date cancels that sale; record a later sale to resell it.
    return_date = models.DateField(null=True, blank=True)
    return_cost = models.DecimalField(**MONEY, default=ZERO,
                                      help_text="Money lost on returns: return label, unrefunded fees, restocking.")
    return_reason = models.CharField(max_length=255, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    MONEY_FIELDS = ("purchase_price", "sale_price", "shipping_charged",
                    "shipping_cost", "platform_fees", "other_sale_costs", "return_cost")

    class Meta:
        ordering = ["-purchase_date", "-pk"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("resell:item-detail", kwargs={"pk": self.pk})

    @property
    def has_active_sale(self):
        """True when sale details are filled in and no return has cancelled that sale."""
        if not self.sale_date or self.sale_price is None:
            return False
        return not (self.return_date and self.return_date >= self.sale_date)

    @property
    def profit(self):
        """Realized profit: set for sold items, a loss for written-off items, otherwise None."""
        if self.status == self.SOLD:
            costs = (self.purchase_price + self.shipping_cost + (self.platform_fees or ZERO)
                     + self.other_sale_costs + self.return_cost)
            return self.sale_price + self.shipping_charged - costs
        if self.status == self.WRITTEN_OFF:
            return -(self.purchase_price + self.return_cost)
        return None

    @property
    def roi_percent(self):
        if self.status != self.SOLD or not self.purchase_price:
            return None
        return (self.profit / self.purchase_price * 100).quantize(Decimal("0.1"))

    @property
    def margin_percent(self):
        if self.status != self.SOLD or not self.sale_price:
            return None
        return (self.profit / self.sale_price * 100).quantize(Decimal("0.1"))

    @property
    def days_to_sell(self):
        if self.status != self.SOLD:
            return None
        return (self.sale_date - self.purchase_date).days

    @property
    def days_in_inventory(self):
        if self.status in (self.SOLD, self.WRITTEN_OFF):
            return None
        return (timezone.localdate() - self.purchase_date).days

    @property
    def listings_to_end(self):
        """Listings still marked active after the item sold or was written off — delist these on each site."""
        if not self.pk or self.status not in (self.SOLD, self.WRITTEN_OFF):
            return Listing.objects.none()
        return self.listings.filter(status=Listing.ACTIVE)

    def compute_status(self):
        if self.written_off_date:
            return self.WRITTEN_OFF
        if self.has_active_sale:
            return self.SOLD
        if self.pk and self.listings.filter(status=Listing.ACTIVE).exists():
            return self.LISTED
        if self.return_date:
            return self.RETURNED
        return self.IN_INVENTORY

    def refresh_status(self):
        """Recompute status after listings change, without re-running save()."""
        status = self.compute_status()
        if status != self.status:
            self.status = status
            Item.objects.filter(pk=self.pk).update(status=status)

    def recalculate_fees(self):
        if self.platform_id and self.sale_price is not None:
            self.platform_fees = self.platform.estimate_fees(self.sale_price + self.shipping_charged)

    def clean(self):
        errors = {}
        for name in self.MONEY_FIELDS:
            value = getattr(self, name)
            if value is not None and value < 0:
                errors[name] = "Must be zero or greater."
        if self.sale_date or self.sale_price is not None:
            for name in ("sale_date", "sale_price", "platform"):
                if getattr(self, name) in (None, ""):
                    errors[name] = "Required to record a sale."
        for name in ("sale_date", "return_date", "written_off_date"):
            value = getattr(self, name)
            if value and self.purchase_date and value < self.purchase_date:
                errors[name] = "Can't be before the purchase date."
        if self.written_off_date and self.has_active_sale:
            errors["written_off_date"] = "A sold item can't also be written off."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.sku == "":
            self.sku = None
        if self.platform_fees is None:
            self.recalculate_fees()
        self.status = self.compute_status()
        super().save(*args, **kwargs)
        if self.status == self.SOLD and self.platform_id:
            # The listing on the site it sold through becomes the sold one; other sites still need delisting.
            self.listings.filter(platform_id=self.platform_id, status=Listing.ACTIVE).update(
                status=Listing.SOLD, ended_date=self.sale_date)


class InventorySettings(models.Model):
    """One row: the item number counter and how the bins are set up. Load it with InventorySettings.load()."""
    FIRST_ITEM_NUMBER = 1001

    next_item_number = models.PositiveIntegerField(default=FIRST_ITEM_NUMBER,
                                                   help_text="The number the next saved item gets. Only goes up.")
    default_bin_capacity = models.PositiveSmallIntegerField(
        default=25, validators=[MinValueValidator(1), MaxValueValidator(99)], help_text="Slots per bin (01–99).")
    bin_capacity_overrides = models.JSONField(
        default=dict, blank=True, help_text='Bins with a different number of slots, e.g. {"K": 12}.')
    disabled_bins = models.JSONField(default=list, blank=True, help_text='Bins never to use, e.g. ["I", "O"].')

    class Meta:
        verbose_name = verbose_name_plural = "bin and SKU settings"

    def __str__(self):
        return "Bin and SKU settings"

    @classmethod
    def load(cls):
        settings_row = cls.objects.filter(pk=1).first()
        if settings_row is None:  # always row 1, so two pages opening at once can't make a second copy
            highest = Item.objects.aggregate(highest=models.Max("item_number"))["highest"]
            settings_row, _ = cls.objects.get_or_create(
                pk=1, defaults={"next_item_number": max(cls.FIRST_ITEM_NUMBER, (highest or 0) + 1)})
        return settings_row

    def clean(self):
        from .locations import is_bin_name  # locations imports this module
        errors = {}
        overrides = self.bin_capacity_overrides
        if not isinstance(overrides, dict) or not all(
                is_bin_name(name) and isinstance(cap, int) and 1 <= cap <= 99 for name, cap in overrides.items()):
            errors["bin_capacity_overrides"] = 'Use capital bin letters and 1–99 slots, e.g. {"K": 12, "AA": 30}.'
        if not isinstance(self.disabled_bins, list) or not all(is_bin_name(name) for name in self.disabled_bins):
            errors["disabled_bins"] = 'Use a list of capital bin letters, e.g. ["I", "O"].'
        highest = Item.objects.aggregate(highest=models.Max("item_number"))["highest"]
        if highest and self.next_item_number <= highest:
            errors["next_item_number"] = f"Must be higher than {highest}, the last number used."
        if errors:
            raise ValidationError(errors)

    def capacity(self, bin_name):
        return self.bin_capacity_overrides.get(bin_name, self.default_bin_capacity)


class Listing(models.Model):
    """One item listed on one site. Cross-listing = several listings for the same item."""
    ACTIVE = "active"
    ENDED = "ended"
    SOLD = "sold"
    STATUS_CHOICES = [(ACTIVE, "Active"), (ENDED, "Ended / delisted"), (SOLD, "Sold here")]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="listings")
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name="listings")
    url = models.URLField("Listing URL", max_length=500, blank=True)
    price = models.DecimalField(**MONEY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    listed_date = models.DateField(default=timezone.localdate)
    ended_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=ACTIVE)

    class Meta:
        ordering = ["-listed_date", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["item", "platform"], condition=models.Q(status="active"),
                                    name="one_active_listing_per_platform"),
        ]

    def __str__(self):
        return f"{self.item} on {self.platform}"

    def clean(self):
        if self.ended_date and self.listed_date and self.ended_date < self.listed_date:
            raise ValidationError({"ended_date": "Can't be before the listed date."})

    def save(self, *args, **kwargs):
        item = self.item
        if self.status == self.ACTIVE and item.status == Item.SOLD and item.platform_id == self.platform_id:
            self.status, self.ended_date = self.SOLD, item.sale_date
        if self.status != self.ACTIVE and not self.ended_date:
            self.ended_date = timezone.localdate()
        super().save(*args, **kwargs)


def photo_path(photo, filename):
    """One folder per upload day, file names led by the SKU: "2026-10-26/A16-1047_IMG_1234.jpg"."""
    sku = photo.item.sku
    return f"{timezone.localdate():%Y-%m-%d}/{get_valid_filename(f'{sku}_{filename}' if sku else filename)}"


def receipt_path(expense, filename):
    return f"receipts/{timezone.localdate():%Y-%m-%d}/{get_valid_filename(filename)}"


PHOTO_MAX_SIDE = 1600  # px on the long side: sharp enough for listings, about 300 KB instead of 3–5 MB


def shrink_photo(upload):
    """A phone photo as a JPEG at most PHOTO_MAX_SIDE px, turned the right way up.
    Re-saving also drops the camera's EXIF data, including the GPS location."""
    with Image.open(upload) as original:
        photo = ImageOps.exif_transpose(original)
        photo.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        if photo.mode != "RGB":  # transparent PNGs go on white rather than black
            background = Image.new("RGB", photo.size, "white")
            background.paste(photo, mask=photo.convert("RGBA").getchannel("A"))
            photo = background
        buffer = io.BytesIO()
        photo.save(buffer, "JPEG", quality=85, optimize=True)
    return ContentFile(buffer.getvalue(), name=f"{Path(upload.name).stem}.jpg")


class ItemImage(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=photo_path, max_length=200)
    caption = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lowest number is shown first (cover photo).")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return f"Image {self.pk} of {self.item}"

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:  # a new upload, not one already on disk
            self.image = shrink_photo(self.image)
        super().save(*args, **kwargs)


def default_mileage_rate():
    return Decimal(settings.RESELL_MILEAGE_RATE)


class Expense(models.Model):
    """Business overhead — not the cost of items bought for resale."""
    SUPPLIES = "supplies"
    SHIPPING_SUPPLIES = "shipping_supplies"
    POSTAGE = "postage"
    MILEAGE = "mileage"
    PAYMENT_PROCESSING = "payment_processing"
    ADVERTISING = "advertising"
    SUBSCRIPTIONS = "subscriptions"
    EQUIPMENT = "equipment"
    STORAGE = "storage"
    PHONE_INTERNET = "phone_internet"
    HOME_OFFICE = "home_office"
    OTHER = "other"
    CATEGORY_CHOICES = [
        (SUPPLIES, "Supplies"), (SHIPPING_SUPPLIES, "Shipping supplies"), (POSTAGE, "Postage"),
        (MILEAGE, "Mileage / travel"), (PAYMENT_PROCESSING, "Payment processing"),
        (ADVERTISING, "Advertising / promoted listings"), (SUBSCRIPTIONS, "Subscriptions / software"),
        (EQUIPMENT, "Equipment"), (STORAGE, "Storage / rent"), (PHONE_INTERNET, "Phone & internet"),
        (HOME_OFFICE, "Home office"), (OTHER, "Other"),
    ]
    RECEIPT_TYPES = ["jpg", "jpeg", "png", "gif", "webp", "heic", "pdf"]

    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=SUPPLIES)
    amount = models.DecimalField(**MONEY, default=ZERO, validators=[MinValueValidator(ZERO)],
                                 help_text="Calculated automatically for mileage.")
    miles = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True,
                                validators=[MinValueValidator(Decimal("0.1"))])
    mileage_rate = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True,
                                       help_text="Dollars per mile. Defaults to the rate in settings.")
    description = models.CharField(max_length=255, blank=True, help_text="For mileage, the trip, e.g. “Goodwill run”.")
    receipt = models.FileField(upload_to=receipt_path, max_length=200, blank=True,
                               validators=[FileExtensionValidator(RECEIPT_TYPES)],
                               help_text="Photo or PDF of the receipt, odometer, etc.")

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.get_category_display()} · ${self.amount} on {self.date}"

    @property
    def receipt_is_image(self):
        return bool(self.receipt) and not self.receipt.name.lower().endswith(".pdf")

    def clean(self):
        if self.category == self.MILEAGE:
            if not self.miles:
                raise ValidationError({"miles": "Enter the miles driven."})
        elif not self.amount:
            raise ValidationError({"amount": "Enter the expense amount."})

    def save(self, *args, **kwargs):
        if self.category == self.MILEAGE and self.miles:
            if self.mileage_rate is None:
                self.mileage_rate = default_mileage_rate()
            self.amount = (self.miles * self.mileage_rate).quantize(CENT, rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)
