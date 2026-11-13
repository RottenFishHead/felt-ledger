from django.contrib import admin
from .models import Location, PokerSession


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "rating")
    list_filter = ("rating",)
    search_fields = ("name", "street_address", "city", "postal_code")


@admin.register(PokerSession)
class PokerSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "started_at", "buy_in", "cash_out")
    list_filter = ("status", "game")
    search_fields = ("user__username", "location__name", "location__city")
