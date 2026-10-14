import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from auctions.models import Player, Team
from tournaments.models import Tournament


class SeedAuctionFromMediaTests(TestCase):
    def setUp(self):
        self.organizer = get_user_model().objects.create_user(
            username="auction_admin",
            password="not-used-in-test",
            is_superuser=True,
        )

    def test_creates_tournament_teams_and_players_from_direct_files_only(self):
        with tempfile.TemporaryDirectory() as temporary_media:
            media_root = Path(temporary_media)
            logo_dir = media_root / "teams" / "logos"
            photo_dir = media_root / "players" / "photos"
            logo_dir.mkdir(parents=True)
            photo_dir.mkdir(parents=True)

            (logo_dir / "SUPER KINGS.png").write_bytes(b"logo")
            (logo_dir / "ROYALS.JPG").write_bytes(b"logo")
            (photo_dir / "Amit Muley.jpeg").write_bytes(b"photo")
            (photo_dir / "Umesh Vispute.heic").write_bytes(b"photo")

            nested_logos = logo_dir / "old-tournament"
            nested_photos = photo_dir / "old-tournament"
            nested_logos.mkdir()
            nested_photos.mkdir()
            (nested_logos / "IGNORED.png").write_bytes(b"logo")
            (nested_photos / "Ignored Player.jpg").write_bytes(b"photo")

            with override_settings(MEDIA_ROOT=media_root):
                call_command(
                    "seed_auction_from_media",
                    organizer=self.organizer.username,
                    tournament_name="Full Auction Test",
                    verbosity=0,
                )

        tournament = Tournament.objects.get(name="Full Auction Test")
        self.assertEqual(tournament.season_year, 2026)
        self.assertEqual(tournament.city, "Pune")
        self.assertEqual(tournament.venue, "ss_ground")
        self.assertEqual(tournament.status, Tournament.Status.PUBLISHED)
        self.assertEqual(tournament.organizer, self.organizer)
        self.assertTrue(hasattr(tournament, "auction"))

        teams = Team.objects.filter(tournament=tournament).order_by("name")
        self.assertEqual(list(teams.values_list("name", flat=True)), ["Royals", "Super Kings"])
        for team in teams:
            self.assertEqual(team.purse_total, Decimal("3000000.00"))
            self.assertEqual(team.purse_remaining, Decimal("3000000.00"))
            self.assertEqual(team.max_players, 10)
            self.assertTrue(team.logo.name.startswith("teams/logos/"))

        players = Player.objects.filter(tournament=tournament).order_by("name")
        self.assertEqual(
            list(players.values_list("name", flat=True)),
            ["Amit Muley", "Umesh Vispute"],
        )
        self.assertEqual(players[0].base_price, Decimal("10000.00"))
        self.assertEqual(players[0].photo.name, "players/photos/Amit Muley.jpeg")
        self.assertEqual(players[1].photo.name, "players/photos/Umesh Vispute.heic")

    def test_missing_direct_player_photos_does_not_create_tournament(self):
        with tempfile.TemporaryDirectory() as temporary_media:
            media_root = Path(temporary_media)
            logo_dir = media_root / "teams" / "logos"
            photo_dir = media_root / "players" / "photos"
            logo_dir.mkdir(parents=True)
            photo_dir.mkdir(parents=True)
            (logo_dir / "ROYALS.png").write_bytes(b"logo")
            (photo_dir / "nested").mkdir()
            (photo_dir / "nested" / "Ignored Player.jpg").write_bytes(b"photo")

            with override_settings(MEDIA_ROOT=media_root):
                with self.assertRaisesMessage(CommandError, "No direct player photo files"):
                    call_command("seed_auction_from_media", verbosity=0)

        self.assertFalse(Tournament.objects.exists())

    def test_dry_run_lists_direct_files_without_database_changes(self):
        with tempfile.TemporaryDirectory() as temporary_media:
            media_root = Path(temporary_media)
            logo_dir = media_root / "teams" / "logos"
            photo_dir = media_root / "players" / "photos"
            logo_dir.mkdir(parents=True)
            photo_dir.mkdir(parents=True)
            (logo_dir / "ROYALS.png").write_bytes(b"logo")
            (photo_dir / "Amit Muley.jpg").write_bytes(b"photo")
            output = StringIO()

            with override_settings(MEDIA_ROOT=media_root):
                call_command(
                    "seed_auction_from_media",
                    dry_run=True,
                    stdout=output,
                    verbosity=0,
                )

        self.assertIn("DRY RUN - no database changes were made", output.getvalue())
        self.assertIn("Teams: 1 direct files", output.getvalue())
        self.assertIn("Players: 1 direct files", output.getvalue())
        self.assertFalse(Tournament.objects.exists())
