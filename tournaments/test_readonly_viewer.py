from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from auctions.models import Auction, AuctionEvent, AuctionLot, Player, Team
from .models import Tournament
from .permissions import TOURNAMENT_VIEWERS_GROUP


class TournamentViewerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("auction-admin", "admin@example.com", "pass123")
        self.viewer = User.objects.create_user("organizer-viewer", password="pass123")
        self.viewer.groups.add(Group.objects.get(name=TOURNAMENT_VIEWERS_GROUP))
        self.tournament = Tournament.objects.create(organizer=self.admin, name="Superuser Cup")
        self.auction = Auction.objects.create(
            tournament=self.tournament, name="Main Auction", code="VIEW-TEST"
        )
        self.team = Team.objects.create(tournament=self.tournament, name="Team One")
        self.player = Player.objects.create(
            tournament=self.tournament, name="Player One", is_eligible_for_auction=True
        )
        self.lot = AuctionLot.objects.create(auction=self.auction, player=self.player, lot_no=1)
        self.client.force_login(self.viewer)

    def test_viewer_can_browse_and_export_superuser_tournament(self):
        urls = [
            reverse("tournament_list"),
            reverse("tournament_detail", args=[self.tournament.slug]),
            reverse("owner_dashboard", args=[self.tournament.slug, self.team.id]),
            reverse("teams_dashboard", args=[self.tournament.slug]),
            reverse("lot_manager", args=[self.tournament.slug]),
            reverse("auction_screen", args=[self.tournament.slug]),
            reverse("auction_stage_public", args=[self.tournament.slug]),
            reverse("auction_updates_public", args=[self.tournament.slug]),
            reverse("players_export_xlsx", args=[self.tournament.slug]),
            reverse("eligible_players_export_xlsx", args=[self.tournament.slug]),
            reverse("lot_bids", args=[self.lot.id]),
            reverse("auction_sold_list", args=[self.auction.id]),
            reverse("auction_unsold_list", args=[self.auction.id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        detail = self.client.get(reverse("tournament_detail", args=[self.tournament.slug]))
        self.assertContains(detail, "Export All Players")
        self.assertNotContains(detail, "Generate Random Lots")
        screen = self.client.get(reverse("auction_screen", args=[self.tournament.slug]))
        self.assertContains(screen, 'class="readonly-portal"')

    def test_viewer_cannot_write_even_with_direct_requests(self):
        urls = [
            reverse("tournament_create"),
            reverse("team_create", args=[self.tournament.slug]),
            reverse("player_create", args=[self.tournament.slug]),
            reverse("generate_random_lots", args=[self.tournament.slug]),
            reverse("move_lot_player", args=[self.tournament.slug]),
            reverse("place_bid", args=[self.lot.id]),
            reverse("end_auction", args=[self.auction.id]),
            reverse("auction_show_control", args=[self.auction.id]),
            reverse("landing"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(Player.objects.filter(tournament=self.tournament).count(), 1)
        self.assertEqual(AuctionLot.objects.filter(auction=self.auction).count(), 1)

    def test_superuser_keeps_full_access(self):
        self.admin.groups.add(Group.objects.get(name=TOURNAMENT_VIEWERS_GROUP))
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("tournament_create"), {"name": "Another Cup", "status": "DRAFT"}
        )
        self.assertNotEqual(response.status_code, 403)
        self.assertTrue(Tournament.objects.filter(name="Another Cup", organizer=self.admin).exists())

    def test_viewer_does_not_create_auction_when_browsing_lots(self):
        self.auction.delete()
        self.assertEqual(
            self.client.get(reverse("lot_manager", args=[self.tournament.slug])).status_code,
            200,
        )
        self.assertFalse(Auction.objects.filter(tournament=self.tournament).exists())

    def test_viewer_sees_only_superuser_owned_tournaments(self):
        other_user = get_user_model().objects.create_user("other-owner", password="pass123")
        other_tournament = Tournament.objects.create(organizer=other_user, name="Private Cup")
        listing = self.client.get(reverse("tournament_list"))
        self.assertContains(listing, self.tournament.name)
        self.assertNotContains(listing, other_tournament.name)
        self.assertEqual(
            self.client.get(reverse("tournament_detail", args=[other_tournament.slug])).status_code,
            404,
        )

    def test_auction_views_do_not_change_auction_data_for_viewer(self):
        urls = [
            reverse("auction_screen", args=[self.tournament.slug]),
            reverse("auction_stage_public", args=[self.tournament.slug]),
            reverse("auction_updates_feed", args=[self.tournament.slug]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(AuctionEvent.objects.filter(auction=self.auction).count(), 0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.status, AuctionLot.Status.PENDING)
