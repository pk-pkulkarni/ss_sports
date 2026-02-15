from django.contrib import admin

# Register your models here.
from django.contrib import admin
from django.utils.html import format_html

from .models import Tournament
from auctions.models import Auction


class AuctionInline(admin.StackedInline):
    """
    Because we have ONE auction per tournament (OneToOne),
    we can manage it directly inside Tournament admin.
    """
    model = Auction
    extra = 0
    max_num = 1
    can_delete = True
    fieldsets = (
        ("Auction", {"fields": ("name", "code", "status", "start_time")}),
        ("Media", {"fields": ("banner",)}),
        ("Rules / Settings", {"fields": ("rules",)}),
    )


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "season_year",
        "status_badge",
        "organizer",
        "city",
        "venue",
        "slug",
        "created_at",
    )
    list_filter = ("status", "season_year", "city")
    search_fields = ("name", "slug", "city", "venue", "organizer__username", "organizer__email")
    readonly_fields = ("slug", "created_at", "updated_at")
    autocomplete_fields = ("organizer",)
    inlines = (AuctionInline,)

    fieldsets = (
        ("Tournament", {"fields": ("organizer", "name", "slug", "status")}),
        ("Details", {"fields": ("season_year", "city", "venue", "start_date", "end_date")}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    actions = ("make_draft", "make_published", "make_archived")

    def status_badge(self, obj: Tournament):
        # small visual badge in list view (admin-friendly)
        color = {
            Tournament.Status.DRAFT: "#6b7280",      # gray
            Tournament.Status.PUBLISHED: "#16a34a",  # green
            Tournament.Status.ARCHIVED: "#0f172a",   # dark
        }.get(obj.status, "#6b7280")
        return format_html(
            '<span style="padding:3px 10px;border-radius:999px;'
            'background:{};color:white;font-weight:600;font-size:12px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def make_draft(self, request, queryset):
        queryset.update(status=Tournament.Status.DRAFT)

    make_draft.short_description = "Set selected tournaments to Draft"

    def make_published(self, request, queryset):
        queryset.update(status=Tournament.Status.PUBLISHED)

    make_published.short_description = "Publish selected tournaments"

    def make_archived(self, request, queryset):
        queryset.update(status=Tournament.Status.ARCHIVED)

    make_archived.short_description = "Archive selected tournaments"
