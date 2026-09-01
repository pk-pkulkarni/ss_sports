from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tournaments.models import Tournament

from .models import Auction, AuctionEvent, AuctionLot, Player, Team, TeamPlayer
from .rules import AUCTION_TYPE_RULE_BASED, RULE_LOT1, RULE_LOT1_AND_LOT2, RULE_PRICE_CAPS


class AuctionRulesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="pass123")
        self.client.force_login(self.user)
        self.tournament = Tournament.objects.create(
            organizer=self.user,
            name="Rules Cup",
            season_year=2026,
        )
        self.auction = Auction.objects.create(
            tournament=self.tournament,
            name="Main Auction",
            code="SS-TEST01",
            status=Auction.Status.DRAFT,
        )

    def create_team(self, name: str, purse: str = "2000000") -> Team:
        return Team.objects.create(
            tournament=self.tournament,
            name=name,
            short_name=name[:3].upper(),
            purse_total=Decimal(purse),
            purse_remaining=Decimal(purse),
            is_active=True,
        )

    def create_lot(self, name: str, lot_no: int, base_price: str = "100000") -> AuctionLot:
        player = Player.objects.create(
            tournament=self.tournament,
            name=name,
            is_active=True,
            base_price=Decimal(base_price),
        )
        return AuctionLot.objects.create(
            auction=self.auction,
            player=player,
            lot_no=lot_no,
            lot_order=AuctionLot.objects.filter(auction=self.auction, lot_no=lot_no).count() + 1,
            status=AuctionLot.Status.PENDING,
            base_price_snapshot=Decimal(base_price),
        )

    def test_setup_auction_rejects_invalid_lot1_division(self):
        for idx in range(7):
            self.create_team(f"Team {idx + 1}")
        for idx in range(21):
            self.create_lot(f"Player {idx + 1}", lot_no=1)

        response = self.client.post(
            reverse("setup_auction", kwargs={"slug": self.tournament.slug}),
            {
                "auction_type": "rule_based",
                "rule_lot1": "1",
                "rule1_lot1_players_per_team": "4",
            },
            follow=True,
        )

        self.auction.refresh_from_db()
        self.assertContains(response, "Use 3 player(s) per team for an exact division.")
        self.assertIsNone(self.auction.rules)

    def test_setup_auction_saves_rule2_configuration(self):
        self.create_team("Team A")
        self.create_team("Team B")
        for idx in range(2):
            self.create_lot(f"Lot1 Player {idx + 1}", lot_no=1)
            self.create_lot(f"Lot2 Player {idx + 1}", lot_no=2)

        response = self.client.post(
            reverse("setup_auction", kwargs={"slug": self.tournament.slug}),
            {
                "auction_type": "rule_based",
                "rule_lot1_and_lot2": "1",
                "rule2_lot1_players_per_team": "1",
                "rule2_lot2_players_per_team": "1",
            },
        )

        self.auction.refresh_from_db()
        self.assertRedirects(response, reverse("auction_screen", kwargs={"slug": self.tournament.slug}))
        self.assertEqual(
            self.auction.rules,
            {
                "auction_type": AUCTION_TYPE_RULE_BASED,
                RULE_LOT1: False,
                RULE_LOT1_AND_LOT2: True,
                RULE_PRICE_CAPS: False,
                "lot1_players_per_team": 1,
                "lot2_players_per_team": 1,
            },
        )

    def test_rule1_keeps_lot1_players_looping_until_sold(self):
        team_a = self.create_team("Team A")
        self.create_team("Team B")
        first_lot = self.create_lot("Lot1 Alpha", lot_no=1)
        second_lot = self.create_lot("Lot1 Beta", lot_no=1)
        self.create_lot("Lot2 Player", lot_no=2)
        self.auction.rules = {
            "auction_type": AUCTION_TYPE_RULE_BASED,
            RULE_LOT1: True,
            RULE_LOT1_AND_LOT2: False,
            RULE_PRICE_CAPS: False,
            "lot1_players_per_team": 1,
            "lot2_players_per_team": None,
        }
        self.auction.save(update_fields=["rules", "updated_at"])

        response1 = self.client.post(reverse("mark_unsold", kwargs={"lot_id": first_lot.id}))
        self.assertEqual(response1.status_code, 200)

        next_after_first = self.client.post(reverse("next_lot", kwargs={"lot_id": first_lot.id}))
        self.assertEqual(next_after_first.status_code, 200)
        self.assertEqual(next_after_first.json()["next"]["lot_no"], 1)
        self.assertEqual(next_after_first.json()["next"]["player"]["name"], "Lot1 Beta")

        bid_response = self.client.post(
            reverse("place_bid", kwargs={"lot_id": second_lot.id}),
            data='{"team_id": %d}' % team_a.id,
            content_type="application/json",
        )
        self.assertEqual(bid_response.status_code, 200)
        sold_response = self.client.post(reverse("mark_sold", kwargs={"lot_id": second_lot.id}))
        self.assertEqual(sold_response.status_code, 200)

        first_lot.refresh_from_db()
        self.assertEqual(first_lot.status, AuctionLot.Status.PENDING)

        next_after_second = self.client.post(reverse("next_lot", kwargs={"lot_id": second_lot.id}))
        self.assertEqual(next_after_second.status_code, 200)
        self.assertEqual(next_after_second.json()["next"]["lot_no"], 1)

    def test_price_cap_blocks_second_700k_player_for_same_team(self):
        team = self.create_team("Team A", purse="3000000")
        retained_lot = self.create_lot("Retained 700k", lot_no=1, base_price="700000")
        blocked_lot = self.create_lot("Auction 700k", lot_no=3, base_price="700000")
        TeamPlayer.objects.create(
            tournament=self.tournament,
            team=team,
            player=retained_lot.player,
            bought_price=Decimal("700000"),
            status=TeamPlayer.Status.ACTIVE,
        )

        self.auction.rules = {
            "auction_type": AUCTION_TYPE_RULE_BASED,
            RULE_LOT1: False,
            RULE_LOT1_AND_LOT2: False,
            RULE_PRICE_CAPS: True,
            "lot1_players_per_team": None,
            "lot2_players_per_team": None,
        }
        self.auction.save(update_fields=["rules", "updated_at"])

        response = self.client.post(
            reverse("place_bid", kwargs={"lot_id": blocked_lot.id}),
            data='{"team_id": %d}' % team.id,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already has a 700000 value player", response.json()["error"])

    def test_updates_feed_can_page_older_events(self):
        for idx in range(105):
            AuctionEvent.objects.create(
                auction=self.auction,
                event_type=AuctionEvent.EventType.BID,
                level=AuctionEvent.Level.BID,
                message=f"Bid event {idx + 1}",
            )

        first_response = self.client.get(
            reverse("auction_updates_feed", kwargs={"slug": self.tournament.slug})
        )
        self.assertEqual(first_response.status_code, 200)
        first_data = first_response.json()
        self.assertEqual(len(first_data["events"]), 100)
        self.assertTrue(first_data["has_older"])

        oldest_loaded_id = min(event["id"] for event in first_data["events"])
        older_response = self.client.get(
            reverse("auction_updates_feed", kwargs={"slug": self.tournament.slug}),
            {"before": oldest_loaded_id},
        )
        self.assertEqual(older_response.status_code, 200)
        older_data = older_response.json()
        self.assertEqual(len(older_data["events"]), 5)
        self.assertFalse(older_data["has_older"])
        self.assertTrue(all(event["id"] < oldest_loaded_id for event in older_data["events"]))

    def test_updates_feed_includes_stage_snapshot(self):
        team = self.create_team("Stage XI")
        live_lot = self.create_lot("Spotlight Player", lot_no=3, base_price="200000")
        AuctionEvent.objects.create(
            auction=self.auction,
            lot=live_lot,
            player=live_lot.player,
            team=team,
            event_type=AuctionEvent.EventType.BID,
            level=AuctionEvent.Level.BID,
            message="Stage XI opened the bidding.",
            amount=Decimal("200000"),
        )

        response = self.client.get(
            reverse("auction_updates_feed", kwargs={"slug": self.tournament.slug})
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("snapshot", data)
        self.assertEqual(data["snapshot"]["auction_code"], self.auction.code)
        self.assertEqual(data["snapshot"]["current_lot"]["player"]["name"], "Spotlight Player")
        self.assertEqual(data["snapshot"]["leaderboard"][0]["name"], "Stage XI")

    def test_public_stage_page_is_accessible_without_login(self):
        self.create_lot("Open Stage Player", lot_no=1)
        self.client.logout()

        response = self.client.get(
            reverse("auction_stage_public", kwargs={"slug": self.tournament.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Auction Stage")
