from django.urls import path
from . import report_views, views

app_name = "resell"

urlpatterns = [
    path("", views.home, name="home"),
    path("help/", views.help_page, name="help"),

    path("items/", views.item_list, name="item-list"),
    path("items/new/", views.item_create, name="item-create"),
    path("items/locations/check/", views.location_check, name="location-check"),
    path("items/locations/next-bin/", views.location_next_bin, name="location-next-bin"),
    path("items/<int:pk>/", views.item_detail, name="item-detail"),
    path("items/<int:pk>/edit/", views.item_edit, name="item-edit"),
    path("items/<int:pk>/delete/", views.item_delete, name="item-delete"),
    path("items/<int:pk>/sold/", views.item_sell, name="item-sell"),
    path("items/<int:pk>/sold/undo/", views.item_sale_undo, name="item-sale-undo"),
    path("items/<int:pk>/shipped/", views.item_ship, name="item-ship"),
    path("items/<int:pk>/shipped/undo/", views.item_ship_undo, name="item-ship-undo"),
    path("items/<int:pk>/returned/", views.item_return, name="item-return"),
    path("items/<int:pk>/returned/fix/", views.item_return_fix, name="item-return-fix"),
    path("items/<int:pk>/write-off/", views.item_write_off, name="item-write-off"),
    path("items/<int:pk>/write-off/undo/", views.item_write_off_undo, name="item-write-off-undo"),

    path("items/<int:item_pk>/listings/new/", views.listing_create, name="listing-create"),
    path("listings/<int:pk>/edit/", views.listing_edit, name="listing-edit"),
    path("listings/<int:pk>/taken-down/", views.listing_end, name="listing-end"),
    path("listings/<int:pk>/delete/", views.listing_delete, name="listing-delete"),

    path("items/<int:item_pk>/photos/", views.photo_add, name="photo-add"),
    path("photos/<int:pk>/main/", views.photo_cover, name="photo-cover"),
    path("photos/<int:pk>/delete/", views.photo_delete, name="photo-delete"),

    path("expenses/", views.expense_list, name="expense-list"),
    path("expenses/new/", views.expense_create, name="expense-create"),
    path("expenses/<int:pk>/edit/", views.expense_edit, name="expense-edit"),
    path("expenses/<int:pk>/delete/", views.expense_delete, name="expense-delete"),

    path("types/", views.category_list, name="category-list"),
    path("types/new/", views.category_create, name="category-create"),
    path("types/<int:pk>/edit/", views.category_edit, name="category-edit"),
    path("types/<int:pk>/delete/", views.category_delete, name="category-delete"),
    path("types/<int:category_pk>/sub-types/new/", views.subcategory_create, name="subcategory-create"),
    path("sub-types/<int:pk>/edit/", views.subcategory_edit, name="subcategory-edit"),
    path("sub-types/<int:pk>/delete/", views.subcategory_delete, name="subcategory-delete"),

    path("places/", views.source_list, name="source-list"),
    path("places/new/", views.source_create, name="source-create"),
    path("places/<int:pk>/edit/", views.source_edit, name="source-edit"),
    path("places/<int:pk>/delete/", views.source_delete, name="source-delete"),

    path("sites/", views.platform_list, name="platform-list"),
    path("sites/new/", views.platform_create, name="platform-create"),
    path("sites/<int:pk>/edit/", views.platform_edit, name="platform-edit"),
    path("sites/<int:pk>/delete/", views.platform_delete, name="platform-delete"),

    path("reports/", report_views.report_index, name="report-index"),
    path("reports/sales-summary/", report_views.sales_summary, name="report-sales-summary"),
    path("reports/sales-by-site/", report_views.sales_by_platform, name="report-sales-by-platform"),
    path("reports/sales-by-category/", report_views.sales_by_category, name="report-sales-by-category"),
    path("reports/profit-per-item/", report_views.sold_items, name="report-sold-items"),
    path("reports/sourcing/", report_views.sourcing, name="report-sourcing"),
    path("reports/sell-through/", report_views.sell_through, name="report-sell-through"),
    path("reports/days-to-sell/", report_views.days_to_sell, name="report-days-to-sell"),
    path("reports/returns/", report_views.returns, name="report-returns"),
    path("reports/top-and-bottom/", report_views.top_bottom, name="report-top-bottom"),

    path("reports/inventory/", report_views.inventory, name="report-inventory"),
    path("reports/aged-inventory/", report_views.aged_inventory, name="report-aged-inventory"),
    path("reports/unlisted/", report_views.unlisted, name="report-unlisted"),
    path("reports/locations/", report_views.locations, name="report-locations"),
    path("reports/cogs/", report_views.cogs, name="report-cogs"),

    path("reports/purchases/", report_views.purchases, name="report-purchases"),
    path("reports/expenses/", report_views.expenses, name="report-expenses"),
    path("reports/site-fees/", report_views.platform_fees, name="report-platform-fees"),
    path("reports/shipping/", report_views.shipping, name="report-shipping"),
    path("reports/mileage/", report_views.mileage, name="report-mileage"),

    path("reports/profit-and-loss/", report_views.profit_loss, name="report-profit-loss"),
    path("reports/cash-flow/", report_views.cash_flow, name="report-cash-flow"),
    path("reports/sales-tax/", report_views.sales_tax, name="report-sales-tax"),
    path("reports/estimated-tax/", report_views.estimated_tax, name="report-estimated-tax"),
    path("reports/schedule-c/", report_views.schedule_c, name="report-schedule-c"),
]
