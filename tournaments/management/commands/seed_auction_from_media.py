import re
import secrets
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from auctions.models import Auction, Player, Team
from tournaments.models import Tournament


IMAGE_EXTENSIONS = {
    ".avif",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}

TEAM_BUDGET = Decimal("3000000.00")
PLAYER_BASE_PRICE = Decimal("10000.00")

TEAM_COLORS = (
    ("#0F172A", "#F59E0B", "#F8FAFC"),
    ("#7F1D1D", "#FBBF24", "#FFF7ED"),
    ("#1E3A8A", "#38BDF8", "#F8FAFC"),
    ("#14532D", "#FACC15", "#F0FDF4"),
    ("#581C87", "#E879F9", "#FAF5FF"),
    ("#7C2D12", "#FB923C", "#FFF7ED"),
    ("#164E63", "#22D3EE", "#ECFEFF"),
    ("#4C0519", "#FB7185", "#FFF1F2"),
    ("#312E81", "#A5B4FC", "#EEF2FF"),
    ("#365314", "#A3E635", "#F7FEE7"),
)

TOURNAMENT_ADJECTIVES = (
    "Blazing",
    "Champions",
    "Electric",
    "Premier",
    "Rising",
    "Super",
    "Thunder",
    "Victory",
)


def display_name_from_file(path: Path) -> str:
    """Turn a media filename into a readable name without using its directories."""
    words = re.sub(r"[_.-]+", " ", path.stem)
    words = " ".join(words.split())
    return words.title()


def direct_image_files(directory: Path) -> list[Path]:
    """Return supported files immediately inside directory; never recurse."""
    if not directory.exists():
        raise CommandError(f"Media directory does not exist: {directory}")
    if not directory.is_dir():
        raise CommandError(f"Media path is not a directory: {directory}")

    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )


def unique_short_name(team_name: str, used: set[str]) -> str:
    words = re.findall(r"[A-Za-z0-9]+", team_name)
    if len(words) > 1:
        base = "".join(word[0] for word in words).upper()
    else:
        base = "".join(words).upper()[:3]
    base = (base or "TEAM")[:12]

    candidate = base
    suffix = 2
    while candidate in used:
        suffix_text = str(suffix)
        candidate = f"{base[:12 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used.add(candidate)
    return candidate


class Command(BaseCommand):
    help = (
        "Create a fresh 2026 Pune auction tournament using team logos and player "
        "photos directly inside MEDIA_ROOT (nested directories are ignored)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--organizer",
            help=(
                "Organizer username. If omitted, the first superuser is used; "
                "if there is no superuser, the first user is used."
            ),
        )
        parser.add_argument(
            "--tournament-name",
            help="Optional exact tournament name instead of a generated random name.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be imported without reading or changing the database.",
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT)
        team_logo_dir = media_root / "teams" / "logos"
        player_photo_dir = media_root / "players" / "photos"

        team_logos = direct_image_files(team_logo_dir)
        player_photos = direct_image_files(player_photo_dir)

        if not team_logos:
            raise CommandError(f"No direct team logo files found in: {team_logo_dir}")
        if not player_photos:
            raise CommandError(f"No direct player photo files found in: {player_photo_dir}")

        tournament_name = options.get("tournament_name") or self._random_tournament_name()

        if options["dry_run"]:
            self._write_dry_run(tournament_name, team_logos, player_photos)
            return

        organizer = self._get_organizer(options.get("organizer"))

        with transaction.atomic():
            tournament = Tournament.objects.create(
                organizer=organizer,
                name=tournament_name,
                season_year=2026,
                city="Pune",
                venue="ss_ground",
                status=Tournament.Status.PUBLISHED,
            )
            auction = Auction.objects.create(
                tournament=tournament,
                name="Main Auction",
                code=self._unique_auction_code(),
                status=Auction.Status.DRAFT,
            )

            used_short_names: set[str] = set()
            for index, logo_path in enumerate(team_logos):
                primary, secondary, accent = TEAM_COLORS[index % len(TEAM_COLORS)]
                team_name = display_name_from_file(logo_path)
                Team.objects.create(
                    tournament=tournament,
                    name=team_name,
                    short_name=unique_short_name(team_name, used_short_names),
                    logo=f"teams/logos/{logo_path.name}",
                    primary_color=primary,
                    secondary_color=secondary,
                    accent_color=accent,
                    purse_total=TEAM_BUDGET,
                    purse_remaining=TEAM_BUDGET,
                    max_players=10,
                    is_active=True,
                )

            for photo_path in player_photos:
                Player.objects.create(
                    tournament=tournament,
                    name=display_name_from_file(photo_path),
                    photo=f"players/photos/{photo_path.name}",
                    city="Pune",
                    base_price=PLAYER_BASE_PRICE,
                    stats={},
                    is_active=True,
                )

        self.stdout.write(self.style.SUCCESS(f"Created tournament: {tournament.name}"))
        self.stdout.write(f"Tournament slug: {tournament.slug}")
        self.stdout.write(f"Auction code: {auction.code}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(team_logos)} teams and {len(player_photos)} players."
            )
        )
        self.stdout.write(
            "Nested folders were ignored. Open the tournament and generate lots when you are ready to test bidding."
        )

    def _get_organizer(self, username):
        user_model = get_user_model()
        if username:
            try:
                return user_model.objects.get(username=username)
            except user_model.DoesNotExist as exc:
                raise CommandError(f'Organizer username "{username}" was not found.') from exc

        organizer = user_model.objects.filter(is_superuser=True).order_by("id").first()
        organizer = organizer or user_model.objects.order_by("id").first()
        if organizer is None:
            raise CommandError(
                "No users exist. Create an organizer first with: python manage.py createsuperuser"
            )
        return organizer

    @staticmethod
    def _random_tournament_name() -> str:
        adjective = secrets.choice(TOURNAMENT_ADJECTIVES)
        suffix = secrets.token_hex(2).upper()
        return f"SS {adjective} Auction {suffix}"

    @staticmethod
    def _unique_auction_code() -> str:
        while True:
            code = f"SS26-{secrets.token_hex(4).upper()}"
            if not Auction.objects.filter(code=code).exists():
                return code

    def _write_dry_run(self, tournament_name, team_logos, player_photos):
        self.stdout.write(self.style.WARNING("DRY RUN - no database changes were made."))
        self.stdout.write(f"Tournament: {tournament_name} (2026), Pune, ss_ground")
        self.stdout.write(
            f"Teams: {len(team_logos)} direct files; budget Rs 30,00,000; max players 10"
        )
        for logo in team_logos:
            self.stdout.write(f"  TEAM   {display_name_from_file(logo)} <- {logo.name}")
        self.stdout.write(f"Players: {len(player_photos)} direct files")
        for photo in player_photos:
            self.stdout.write(f"  PLAYER {display_name_from_file(photo)} <- {photo.name}")
