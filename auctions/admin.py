from django.db import models, transaction


# Register your models here.
from django.contrib import admin
from django.utils.html import format_html

from .models import Auction, Team, Player, AuctionLot, Bid, TeamPlayer, AuctionEvent
from .services import generate_lots_from_active_players as generate_lots_for_auction


# ---------- Helpers (thumbnails) ----------

def _thumb(image_field, size=44):
    if not image_field:
        return "—"
    try:
        return format_html(
            '<img src="{}" style="width:{}px;height:{}px;object-fit:cover;border-radius:10px;'
            'box-shadow:0 6px 18px rgba(0,0,0,.15);" />',
            image_field.url,
            size,
            size,
        )
    except Exception:
        # In case file is missing on disk but DB has path
        return "—"


# ---------- Inlines ----------

class BidInline(admin.TabularInline):
    model = Bid
    extra = 0
    fields = ("team", "amount", "bid_time", "is_valid")
    readonly_fields = ("bid_time",)
    ordering = ("-bid_time",)
    autocomplete_fields = ("team",)


class AuctionLotInline(admin.TabularInline):
    model = AuctionLot
    extra = 0
    fields = ("lot_no", "lot_order", "player", "status", "base_price_snapshot", "sold_to_team", "sold_price")
    autocomplete_fields = ("player", "sold_to_team")
    ordering = ("lot_no", "lot_order")


class TeamPlayerInline(admin.TabularInline):
    model = TeamPlayer
    extra = 0
    fields = ("player", "status", "bought_price", "jersey_no")
    autocomplete_fields = ("player",)
    ordering = ("player__name",)


# ---------- Admins ----------

@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    list_display = ("id", "tournament", "name", "code", "status", "start_time", "banner_thumb", "updated_at")
    list_filter = ("status", "tournament__season_year")
    search_fields = ("name", "code", "tournament__name")
    readonly_fields = ("created_at", "updated_at", "banner_preview")
    inlines = (AuctionLotInline,)
    fieldsets = (
        ("Auction", {"fields": ("tournament", "name", "code", "status", "start_time")}),
        ("Media", {"fields": ("banner", "banner_preview")}),
        ("Rules / Settings", {"fields": ("rules",)}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    actions = ("generate_lots_from_active_players",)

    def banner_thumb(self, obj):
        return _thumb(obj.banner, size=44)

    banner_thumb.short_description = "Banner"

    def banner_preview(self, obj):
        return _thumb(obj.banner, size=240)

    banner_preview.short_description = "Banner preview"

    @admin.action(description="Generate lots from ACTIVE players (auto lot_no)")
    def generate_lots_from_active_players(self, request, queryset):
        created_total = 0
        skipped_total = 0

        for auction in queryset.select_related("tournament"):
            created, skipped = generate_lots_for_auction(auction)
            created_total += created
            skipped_total += skipped

        self.message_user(
            request,
            f"✅ Lots generated. Created: {created_total}. Skipped (already existed): {skipped_total}.",
        )


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("id", "logo_thumb", "name", "short_name", "tournament", "purse_total", "purse_remaining", "is_active")
    list_filter = ("is_active", "tournament__season_year", "tournament")
    search_fields = ("name", "short_name", "tournament__name")
    readonly_fields = ("created_at", "updated_at", "logo_preview")
    autocomplete_fields = ("tournament",)
    inlines = (TeamPlayerInline,)
    fieldsets = (
        ("Team", {"fields": ("tournament", "name", "short_name", "is_active")}),
        ("Logo", {"fields": ("logo", "logo_preview")}),
        ("Purse", {"fields": ("purse_total", "purse_remaining", "min_players", "max_players")}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    def logo_thumb(self, obj):
        return _thumb(obj.logo, size=44)

    logo_thumb.short_description = "Logo"

    def logo_preview(self, obj):
        return _thumb(obj.logo, size=240)

    logo_preview.short_description = "Logo preview"


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "photo_thumb",
        "name",
        "role",
        "tournament",
        "base_price",
        "reserve_price",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "role", "tournament__season_year", "tournament")
    search_fields = ("name", "phone", "city", "tournament__name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "photo_preview",
        "payment_screenshot_preview",
    )
    autocomplete_fields = ("tournament",)

    fieldsets = (
        ("Player", {"fields": ("tournament", "name", "is_active", "photo", "photo_preview")}),
        (
            "Registration",
            {
                "fields": (
                    "gender",
                    "dob",
                    "building",
                    "flat_no",
                    "jersey_no",
                    "jersey_name",
                    "jersey_size",
                    "payment_screenshot",
                    "payment_screenshot_preview",
                )
            },
        ),
        ("Profile", {"fields": ("phone", "city", "age", "role", "batting_style", "bowling_style")}),
        ("Auction Pricing", {"fields": ("base_price", "reserve_price")}),
        ("Stats (editable)", {"fields": ("stats",)}),
        ("Notes", {"fields": ("notes",)}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    def photo_thumb(self, obj):
        return _thumb(obj.photo, size=44)

    photo_thumb.short_description = "Photo"

    def photo_preview(self, obj):
        return _thumb(obj.photo, size=240)

    photo_preview.short_description = "Photo preview"

    def payment_screenshot_preview(self, obj):
        return _thumb(obj.payment_screenshot, size=240)

    payment_screenshot_preview.short_description = "Payment screenshot preview"


@admin.register(AuctionLot)
class AuctionLotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "auction",
        "lot_no",
        "lot_order",
        "player",
        "status",
        "base_price_snapshot",
        "sold_to_team",
        "sold_price",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "auction", "auction__tournament")
    search_fields = ("player__name", "sold_to_team__name", "auction__code", "auction__tournament__name")
    autocomplete_fields = ("auction", "player", "sold_to_team")
    inlines = (BidInline,)
    ordering = ("auction", "lot_no", "lot_order")


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("id", "lot", "team", "amount", "bid_time", "is_valid")
    list_filter = ("is_valid", "team", "lot__auction")
    search_fields = ("team__name", "lot__player__name", "lot__auction__code")
    autocomplete_fields = ("lot", "team")
    ordering = ("-bid_time",)
    readonly_fields = ("bid_time",)


@admin.register(TeamPlayer)
class TeamPlayerAdmin(admin.ModelAdmin):
    list_display = ("id", "tournament", "team", "player", "status", "bought_price", "jersey_no")
    list_filter = ("status", "tournament", "team")
    search_fields = ("player__name", "team__name", "tournament__name")
    autocomplete_fields = ("tournament", "team", "player", "bought_in_auction")
    ordering = ("team__name", "player__name")


@admin.register(AuctionEvent)
class AuctionEventAdmin(admin.ModelAdmin):
    list_display = ("id", "auction", "event_type", "level", "message", "amount", "created_at")
    list_filter = ("event_type", "level", "auction")
    search_fields = ("message", "auction__code", "auction__tournament__name", "player__name", "team__name")
    autocomplete_fields = ("auction", "lot", "player", "team")
    ordering = ("-id",)
