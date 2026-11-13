from django.contrib import admin
from django.shortcuts import redirect
from .forms import ItemForm
from .models import (Brand, Category, Expense, InventorySettings, Item, ItemImage, Listing, Platform, Source,
                     SubCategory)


@admin.register(Brand)
class NameAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class SubCategoryInline(admin.TabularInline):
    model = SubCategory
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    inlines = [SubCategoryInline]


@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category")
    list_filter = ("category",)
    search_fields = ("name", "category__name")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "city", "state")
    list_filter = ("kind", "state")
    search_fields = ("name", "street_address", "city", "postal_code")


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("name", "fee_percent", "fee_fixed")


class ListingInline(admin.TabularInline):
    model = Listing
    extra = 1


class ItemImageInline(admin.TabularInline):
    model = ItemImage
    extra = 1


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    form = ItemForm
    list_display = ("title", "brand", "status", "purchase_date", "purchase_price", "sale_price", "profit")
    list_filter = ("status", "condition", "platform", "subcategories__category")
    search_fields = ("title", "sku", "brand__name", "storage_location")
    filter_horizontal = ("subcategories",)
    readonly_fields = ("status", "item_number", "location_history")
    inlines = [ListingInline, ItemImageInline]


@admin.register(InventorySettings)
class InventorySettingsAdmin(admin.ModelAdmin):
    """Only one row, so the list page goes straight to it."""
    fields = ("default_bin_capacity", "bin_capacity_overrides", "disabled_bins", "next_item_number")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect("admin:resell_inventorysettings_change", InventorySettings.load().pk)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "category", "amount", "miles", "description")
    list_filter = ("category",)
