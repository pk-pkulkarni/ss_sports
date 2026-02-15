import os
from pathlib import Path

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from django.db import transaction
from django.core.files import File

from tournaments.models import Tournament
from auctions.models import Auction, Team, Player


TEAM_NAMES = [
    "SS Super Kings",
    "SS Superstars",
    "SS Hurricanes",
    "SS Royals",
    "SS Challengers",
    "SS Indians",
    "SS Legends",
]

# PLAYER_NAMES = [
#     "Omkar Mhaskar",
#     "Mohnish Shah",
#     "Rajat Deokar",
#     "Shripad Upadhye",
#     "Gautam Chavan",
#     "Rajat Datkhore",
#     "Siddharth Dixit",
#     "Akshay Ladha",
#     "Umesh Vispute",
#     "Vedant Barawake",
#     "Abhijit Phatak",
#     "Aditya Morgaonkar",
#     "Ketan Lele",
#     "Chaitanya Jadhav",
#     "Sachin Shinde",
#     "Amit Muley",
#     "gaurav Tikhge",
#     "Raghav Pathade",
#     "Mandar Tavare",
#     "Prasad Kulkarni - D5",
#     "Gitesh Kendurkar",
#     "Siddhant Kumar",
#     "Ravi Bane",
#     "Aditya Khare",
#     "Saket Kagalkar",
#     "Vishnu Shinde",
#     "Rutwij Salapurikar",
#     "Shailendra Bhangurkar",
#     "Kaushik Hasanbis",
#     "Amit Satpute",
#     "Basavraj Patil",
#     "Hrishikesh Ghanate",
#     "Tanay Chounde",
#     "Sunil Nandedkar",
#     "Sharvil Jadhav",
#     "Devendra Kulkarni",
#     "Vaibhav Vispute",
#     "Arya Thanekar",
#     "Saurabh Joshi",
#     "Mohit Joshi",
#     "Aditya Phatak",
#     "Jr.Nikhil Deshmukh",
#     "Dhiraj Somani",
#     "Prasad Kulkarni - D8",
#     "Rohit Walimbe",
#     "Manohar Shetty",
#     "Shriraj Bhandari",
#     "Tanmesh Shah",
#     "Kapil Joshi",
#     "Nikhil Tambe",
#     "Amol Abhyankar",
#     "Anshuman Shinde",
# ]


# Your media photos folder (Windows path)
PHOTOS_DIR = r"D:\ai_projects\cricket_auction\media\players\photos"


def name_from_filename(filename: str) -> str:
    """
    Convert a filename into a readable player name.
    Examples:
      "Omkar Mhaskar.jpg" -> "Omkar Mhaskar"
      "Prasad Kulkarni - D5.png" -> "Prasad Kulkarni - D5"
      "jr.nikhil_deshmukh.jpeg" -> "Jr Nikhil Deshmukh"
    """
    stem = Path(filename).stem

    # Convert common separators to spaces
    stem = stem.replace("_", " ").replace(".", " ").replace("-", "-")  # keep hyphen if present already

    # Normalize multi spaces
    stem = " ".join(stem.split())

    # Title-case but keep tokens like D5, D8
    parts = []
    for w in stem.split(" "):
        if w.upper() in {"D5", "D8"}:
            parts.append(w.upper())
        else:
            parts.append(w[:1].upper() + w[1:].lower() if w else w)

    # Fix "Jr" special case if you used jr
    if parts and parts[0].lower() == "jr":
        parts[0] = "Jr"

    return " ".join(parts)


class Command(BaseCommand):
    help = "Seed demo data: 1 Tournament + 1 Auction + 5 Teams + Players from photos directory (auto name + photo attach)"

    def add_arguments(self, parser):
        parser.add_argument("--season", type=int, default=2026)
        parser.add_argument("--tournament-name", type=str, default="SS Cricket Auction")
        parser.add_argument("--city", type=str, default="Pune")
        parser.add_argument("--venue", type=str, default="Sundar Sanskruti Ground")
        parser.add_argument("--reset", action="store_true", help="Delete existing tournament with same slug and recreate.")

        # Optional: if you still want to limit how many photos/players to import
        parser.add_argument("--max", type=int, default=0, help="Max players to import from photos (0 = all).")

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()
        organizer = User.objects.order_by("id").first()
        if not organizer:
            raise SystemExit("No users found. Create a superuser first: python manage.py createsuperuser")

        tournament_name = opts["tournament_name"]
        season = opts["season"]
        city = opts["city"]
        venue = opts["venue"]
        max_count = int(opts["max"] or 0)

        slug_base = slugify(f"{tournament_name}-{season}") or "ss-auction"
        slug = slug_base

        existing = Tournament.objects.filter(slug=slug).first()
        if existing and opts["reset"]:
            self.stdout.write(self.style.WARNING(f"Reset enabled. Deleting tournament: {existing}"))
            existing.delete()
            existing = None

        if existing:
            tournament = existing
            self.stdout.write(self.style.WARNING(f"Using existing tournament: {tournament}"))
        else:
            tournament = Tournament.objects.create(
                organizer=organizer,
                name=tournament_name,
                season_year=season,
                city=city,
                venue=venue,
                slug=slug,
                status=Tournament.Status.PUBLISHED,
            )
            self.stdout.write(self.style.SUCCESS(f"Created tournament: {tournament}"))

        # Auction (OneToOne)
        auction = getattr(tournament, "auction", None)
        if not auction:
            code = f"SS-{season}-{get_random_string(6).upper()}"
            auction = Auction.objects.create(
                tournament=tournament,
                name="Main Auction",
                code=code,
                status=Auction.Status.DRAFT,
            )
            self.stdout.write(self.style.SUCCESS(f"Created auction: {auction} (code={auction.code})"))
        else:
            self.stdout.write(self.style.WARNING(f"Auction already exists for this tournament: {auction}"))

        # Teams - ensure (no duplicates)
        created_teams = 0
        for name in TEAM_NAMES:
            # this already ensures: "if exists, do not add"
            obj, created = Team.objects.get_or_create(
                tournament=tournament,
                name=name,
                defaults={
                    "short_name": "".join([w[0] for w in name.split()]).upper()[:8],
                    "purse_total": 0,
                    "purse_remaining": 0,
                    "is_active": True,
                },
            )
            if created:
                created_teams += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Teams ensured. Newly created: {created_teams}. Total: {tournament.teams.count()}"
            )
        )

        # Players from photos directory
        photos_path = Path(PHOTOS_DIR)
        if not photos_path.exists():
            raise SystemExit(f"Photos directory not found: {PHOTOS_DIR}")

        allowed_ext = {".jpg", ".jpeg", ".png", ".webp"}
        photo_files = [p for p in photos_path.iterdir() if p.is_file() and p.suffix.lower() in allowed_ext]
        photo_files.sort(key=lambda p: p.name.lower())

        if max_count > 0:
            photo_files = photo_files[:max_count]

        if not photo_files:
            self.stdout.write(self.style.WARNING(f"No photo files found in: {PHOTOS_DIR}"))
            self.stdout.write(self.style.SUCCESS("✅ Seed completed (no players imported)."))
            return

        created_players = 0
        updated_photos = 0

        for photo in photo_files:
            player_name = name_from_filename(photo.name)

            player, created = Player.objects.get_or_create(
                tournament=tournament,
                name=player_name,
                defaults={
                    "base_price": 0,
                    "is_active": True,
                    "stats": {},
                },
            )
            if created:
                created_players += 1

            # Attach photo if missing OR if filename differs (optional update)
            # We will set it if empty.
            if not player.photo:
                with photo.open("rb") as f:
                    # store into MEDIA_ROOT/players/photos/<filename>
                    player.photo.save(photo.name, File(f), save=True)
                updated_photos += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Players ensured from photos. Newly created: {created_players}. Photos attached: {updated_photos}. "
                f"Total players in tournament: {tournament.players.count()}"
            )
        )

        self.stdout.write(self.style.SUCCESS("✅ Seed completed successfully."))
