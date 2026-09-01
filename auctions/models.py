from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from tournaments.models import Tournament

HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message="Enter a valid hex color like #22C55E.",
)


def team_logo_upload_to(instance, filename: str) -> str:
    # media/teams/logos/<tournament_id>/<filename>
    return f"teams/logos/{instance.tournament_id}/{filename}"


def player_photo_upload_to(instance, filename: str) -> str:
    # media/players/photos/<tournament_id>/<filename>
    return f"players/photos/{instance.tournament_id}/{filename}"


def player_payment_screenshot_upload_to(instance, filename: str) -> str:
    # media/players/payments/<tournament_id>/<filename>
    return f"players/payments/{instance.tournament_id}/{filename}"


def auction_banner_upload_to(instance, filename: str) -> str:
    # media/auctions/banners/<tournament_id>/<filename>
    return f"auctions/banners/{instance.tournament_id}/{filename}"


class Auction(models.Model):
    """
    One auction per tournament (enforced via OneToOneField).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        LIVE = "LIVE", "Live"
        PAUSED = "PAUSED", "Paused"
        CLOSED = "CLOSED", "Closed"

    tournament = models.OneToOneField(
        Tournament,
        on_delete=models.CASCADE,
        related_name="auction",
    )

    name = models.CharField(max_length=120, default="Main Auction")
    code = models.CharField(
        max_length=30,
        unique=True,
        help_text="Shareable code for organizers/viewers to open auction quickly.",
    )

    start_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    banner = models.ImageField(upload_to=auction_banner_upload_to, null=True, blank=True)

    # Optional auction rules/settings (increments, min bid, caps etc.)
    rules = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.tournament} — {self.name}"

    class Meta:
        db_table = "auction"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["code"]),
        ]


class Team(models.Model):
    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="teams",
    )

    name = models.CharField(max_length=120)
    short_name = models.CharField(max_length=12, null=True, blank=True)
    logo = models.ImageField(upload_to=team_logo_upload_to, null=True, blank=True)
    tagline = models.CharField(max_length=140, blank=True)
    primary_color = models.CharField(
        max_length=7,
        default="#0F172A",
        validators=[HEX_COLOR_VALIDATOR],
    )
    secondary_color = models.CharField(
        max_length=7,
        default="#22C55E",
        validators=[HEX_COLOR_VALIDATOR],
    )
    accent_color = models.CharField(
        max_length=7,
        default="#F97316",
        validators=[HEX_COLOR_VALIDATOR],
    )

    purse_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    purse_remaining = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    min_players = models.PositiveIntegerField(null=True, blank=True)
    max_players = models.PositiveIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.name}"

    class Meta:
        db_table = "team"
        unique_together = (("tournament", "name"),)
        indexes = [
            models.Index(fields=["tournament", "is_active"]),
        ]


class TeamAccess(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        CO_OWNER = "CO_OWNER", "Co-Owner"
        ANALYST = "ANALYST", "Analyst"
        VIEWER = "VIEWER", "Viewer"

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="access_entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_access_entries",
    )
    linked_player = models.ForeignKey(
        "Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_access_entries",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.OWNER)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.is_primary and not self.is_active:
            raise ValidationError({"is_active": "Primary access must stay active."})

        if self.linked_player_id and self.team_id and self.linked_player.tournament_id != self.team.tournament_id:
            raise ValidationError({"linked_player": "Linked player must belong to the same tournament as the team."})

        if self.is_primary and self.team_id:
            existing_primary = TeamAccess.objects.filter(
                team_id=self.team_id,
                is_primary=True,
                is_active=True,
            ).exclude(pk=self.pk)
            if existing_primary.exists():
                raise ValidationError({"is_primary": "This team already has an active primary access entry."})

    def __str__(self) -> str:
        return f"{self.team} - {self.user} ({self.get_role_display()})"

    class Meta:
        db_table = "team_access"
        unique_together = (("team", "user"),)
        indexes = [
            models.Index(fields=["team", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]


class TeamWatchlist(models.Model):
    class Priority(models.TextChoices):
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="watchlist_entries",
    )
    player = models.ForeignKey(
        "Player",
        on_delete=models.CASCADE,
        related_name="watchlist_entries",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_watchlist_entries",
    )
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    note = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.team_id and self.player_id and self.player.tournament_id != self.team.tournament_id:
            raise ValidationError({"player": "Watchlist player must belong to the same tournament as the team."})

    def __str__(self) -> str:
        return f"{self.team} watchlist - {self.player}"

    class Meta:
        db_table = "team_watchlist"
        unique_together = (("team", "player"),)
        indexes = [
            models.Index(fields=["team", "priority"]),
            models.Index(fields=["player"]),
        ]


class Player(models.Model):
    class Role(models.TextChoices):
        BAT = "BAT", "Batsman"
        BOWL = "BOWL", "Bowler"
        AR = "AR", "All-Rounder"
        WK = "WK", "Wicket-Keeper"

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        OTHER = "OTHER", "Other"
        NA = "NA", "Prefer not to say"

    class JerseySize(models.TextChoices):
        XS = "XS", "XS"
        S = "S", "S"
        M = "M", "M"
        L = "L", "L"
        XL = "XL", "XL"
        XXL = "XXL", "XXL"

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="players",
    )

    name = models.CharField(max_length=140)
    photo = models.ImageField(upload_to=player_photo_upload_to, null=True, blank=True)

    # Registration/payment info
    payment_screenshot = models.ImageField(
        upload_to=player_payment_screenshot_upload_to,
        null=True,
        blank=True,
    )

    # Jersey details
    jersey_no = models.PositiveIntegerField(null=True, blank=True)
    jersey_name = models.CharField(max_length=30, null=True, blank=True)
    jersey_size = models.CharField(
        max_length=4, choices=JerseySize.choices, null=True, blank=True
    )

    phone = models.CharField(max_length=20, null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, null=True, blank=True)

    building = models.CharField(max_length=20, null=True, blank=True)
    flat_no = models.CharField(max_length=20, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)

    city = models.CharField(max_length=80, null=True, blank=True)

    role = models.CharField(max_length=8, choices=Role.choices, null=True, blank=True)

    batting_style = models.CharField(max_length=40, null=True, blank=True)
    bowling_style = models.CharField(max_length=40, null=True, blank=True)

    age = models.PositiveIntegerField(null=True, blank=True)

    # Auction pricing fields
    base_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    reserve_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    # "Admin page will be able to update register player stat"
    # Store flexible stats like:
    # {"matches": 12, "runs": 340, "wickets": 9, "best": "55 (32)"}
    stats = models.JSONField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        db_table = "player"
        indexes = [
            models.Index(fields=["tournament", "is_active"]),
            models.Index(fields=["tournament", "role"]),
        ]


class AuctionLot(models.Model):
    """
    A player as a lot in an auction.
    Tracks SOLD/UNSOLD and final sale info.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SOLD = "SOLD", "Sold"
        UNSOLD = "UNSOLD", "Unsold"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    auction = models.ForeignKey(
        Auction,
        on_delete=models.CASCADE,
        related_name="lots",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="lots",
    )

    lot_no = models.PositiveIntegerField()
    lot_order = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)

    # Snapshot base price at the time of auction
    base_price_snapshot = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    sold_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    sold_to_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_lots",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Auction {self.auction_id} Lot {self.lot_no}.{self.lot_order} — {self.player}"

    class Meta:
        db_table = "auction_lot"
        unique_together = (
            ("auction", "player"),
        )
        indexes = [
            models.Index(fields=["auction", "status"]),
            models.Index(fields=["auction", "lot_no", "lot_order"]),
        ]


class AuctionEvent(models.Model):
    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        BID = "BID", "Bid"
        MILESTONE = "MILESTONE", "Milestone"
        SOLD = "SOLD", "Sold"
        UNSOLD = "UNSOLD", "Unsold"

    class EventType(models.TextChoices):
        LOT_START = "LOT_START", "Lot Start"
        BID = "BID", "Bid"
        MILESTONE_1L = "MILESTONE_1L", "Milestone 1L"
        SOLD = "SOLD", "Sold"
        UNSOLD = "UNSOLD", "Unsold"

    auction = models.ForeignKey(
        Auction,
        on_delete=models.CASCADE,
        related_name="events",
    )
    lot = models.ForeignKey(
        AuctionLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    event_type = models.CharField(max_length=20, choices=EventType.choices)
    level = models.CharField(max_length=12, choices=Level.choices, default=Level.INFO)
    message = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auction_event"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["auction", "created_at"]),
            models.Index(fields=["auction", "event_type"]),
        ]


class Bid(models.Model):
    """
    Bid history (audit-friendly).
    """

    lot = models.ForeignKey(
        AuctionLot,
        on_delete=models.CASCADE,
        related_name="bids",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="bids",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    bid_time = models.DateTimeField(auto_now_add=True)

    # optional: who clicked/placed the bid (admin/operator)
    created_by_user_id = models.BigIntegerField(null=True, blank=True)

    is_valid = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.team} bid {self.amount} on lot {self.lot_id}"

    class Meta:
        db_table = "bid"
        indexes = [
            models.Index(fields=["lot", "bid_time"]),
            models.Index(fields=["team"]),
        ]


class TeamPlayer(models.Model):
    """
    Final tournament squad (source of truth after sale).
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"
        INJURED = "INJURED", "Injured"
        BENCH = "BENCH", "Bench"

    class Designation(models.TextChoices):
        PLAYER = "PLAYER", "PLAYER"
        OWNER = "OWNER", "OWNER"
        CAPTAIN = "CAPTAIN", "CAPTAIN"
        MARQUEE = "MARQUEE", "MARQUEE"

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="team_players",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="squad",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="team_entries",
    )

    bought_in_auction = models.ForeignKey(
        Auction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_players",
    )
    bought_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    designation = models.CharField(max_length=50, choices=Designation.choices, default=Designation.PLAYER)
    jersey_no = models.PositiveIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.team} — {self.player}"

    class Meta:
        db_table = "team_player"
        unique_together = (("tournament", "player"),)
        indexes = [
            models.Index(fields=["tournament", "team"]),
            models.Index(fields=["team", "status"]),
        ]
