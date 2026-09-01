from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from auctions.models import Auction, AuctionLot, Bid, Player, Team, TeamAccess, TeamPlayer, TeamWatchlist

from .forms import DEFAULT_PLAYER_BASE_PRICE, PlayerForm, PublicPlayerRegistrationForm
from .models import Tournament


class PlayerBasePriceFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="pass123")
        self.tournament = Tournament.objects.create(
            organizer=self.user,
            name="Base Price Cup",
            season_year=2026,
        )

    def test_new_player_form_defaults_base_price_to_10k(self):
        form = PlayerForm()
        self.assertEqual(form.fields["base_price"].initial, DEFAULT_PLAYER_BASE_PRICE)

    def test_edit_player_form_uses_database_base_price(self):
        player = Player.objects.create(
            tournament=self.tournament,
            name="Existing Player",
            base_price=25000,
            is_active=True,
        )

        form = PlayerForm(instance=player)
        self.assertEqual(form["base_price"].value(), 25000)

    def test_public_registration_defaults_base_price_to_10k(self):
        form = PublicPlayerRegistrationForm(
            data={
                "tournament": self.tournament.pk,
                "name": "Registered Player",
                "gender": Player.Gender.MALE,
                "building": "D-1",
                "flat_no": "101",
                "dob": "2000-01-01",
                "phone": "9999999999",
                "jersey_no": 7,
                "jersey_name": "REG",
                "jersey_size": Player.JerseySize.M,
            },
            files={
                "payment_screenshot": SimpleUploadedFile(
                    "payment.gif",
                    (
                        b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                        b"\x00\x00\x00\xff\xff\xff!\xf9\x04"
                        b"\x01\x00\x00\x00\x00,\x00\x00\x00"
                        b"\x00\x01\x00\x01\x00\x00\x02\x02D"
                        b"\x01\x00;"
                    ),
                    content_type="image/gif",
                )
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        player = form.save()
        self.assertEqual(player.base_price, DEFAULT_PLAYER_BASE_PRICE)


class TeamOwnerDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.organizer = User.objects.create_user(username="organizer", password="pass123")
        self.owner = User.objects.create_user(username="teamowner", password="pass123")
        self.outsider = User.objects.create_user(username="outsider", password="pass123")

        self.tournament = Tournament.objects.create(
            organizer=self.organizer,
            name="Owner Portal Cup",
            season_year=2026,
        )
        self.auction = Auction.objects.create(
            tournament=self.tournament,
            name="Main Auction",
            code="SS-OWNER1",
            status=Auction.Status.LIVE,
            rules={"auction_type": "rule_based", "rule_lot1": True, "lot1_players_per_team": 2},
        )
        self.team = Team.objects.create(
            tournament=self.tournament,
            name="Sky Strikers",
            short_name="SSK",
            purse_total=2000000,
            purse_remaining=1750000,
            max_players=8,
            tagline="Own the night.",
            primary_color="#102A43",
            secondary_color="#2EC4B6",
            accent_color="#FF9F1C",
        )
        TeamAccess.objects.create(
            team=self.team,
            user=self.owner,
            role=TeamAccess.Role.OWNER,
            is_primary=True,
            is_active=True,
            linked_player=None,
        )

        owner_player = Player.objects.create(
            tournament=self.tournament,
            name="Owner On Team",
            role=Player.Role.WK,
            base_price=90000,
            is_active=True,
        )
        bought_player = Player.objects.create(
            tournament=self.tournament,
            name="Captain Cool",
            role=Player.Role.BAT,
            base_price=100000,
            is_active=True,
        )
        current_player = Player.objects.create(
            tournament=self.tournament,
            name="Live Prospect",
            role=Player.Role.AR,
            base_price=120000,
            is_active=True,
        )

        AuctionLot.objects.create(
            auction=self.auction,
            player=bought_player,
            lot_no=1,
            lot_order=1,
            status=AuctionLot.Status.SOLD,
            base_price_snapshot=100000,
            sold_to_team=self.team,
            sold_price=250000,
        )
        running_lot = AuctionLot.objects.create(
            auction=self.auction,
            player=current_player,
            lot_no=1,
            lot_order=2,
            status=AuctionLot.Status.RUNNING,
            base_price_snapshot=120000,
        )

        TeamPlayer.objects.create(
            tournament=self.tournament,
            team=self.team,
            player=owner_player,
            bought_price=80000,
            designation=TeamPlayer.Designation.OWNER,
        )

        TeamPlayer.objects.create(
            tournament=self.tournament,
            team=self.team,
            player=bought_player,
            bought_in_auction=self.auction,
            bought_price=250000,
            designation=TeamPlayer.Designation.CAPTAIN,
        )

        access = TeamAccess.objects.get(team=self.team, user=self.owner)
        access.linked_player = owner_player
        access.save(update_fields=["linked_player"])

        self.owner_player = owner_player
        self.bought_player = bought_player
        self.running_lot = running_lot

    def test_owner_can_view_owner_dashboard(self):
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sky Strikers")
        self.assertContains(response, "Captain Cool")
        self.assertContains(response, "Live Prospect")
        self.assertContains(response, "Can Bid")

    def test_owner_dashboard_shows_phase3_strategy_panels(self):
        self.client.force_login(self.owner)
        Bid.objects.create(
            lot=self.running_lot,
            team=self.team,
            amount=120000,
            is_valid=True,
        )

        response = self.client.get(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Spending Plan")
        self.assertContains(response, "Watchlist")
        self.assertContains(response, "Demand Watch")
        self.assertNotContains(response, "Role Gaps")
        self.assertNotContains(response, "Bargain Candidates")
        self.assertNotContains(response, "Stage Mode")
        self.assertNotContains(response, "Edit Team")

    def test_owner_can_add_and_remove_watchlist_player(self):
        self.client.force_login(self.owner)
        target = Player.objects.create(
            tournament=self.tournament,
            name="Watch Target",
            role=Player.Role.BOWL,
            base_price=50000,
            is_active=True,
        )

        response = self.client.post(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
            data={
                "action": "add_watchlist",
                "player_id": target.id,
                "priority": TeamWatchlist.Priority.HIGH,
                "note": "Death overs option",
            },
        )

        self.assertRedirects(
            response,
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
        )
        entry = TeamWatchlist.objects.get(team=self.team, player=target)
        self.assertEqual(entry.priority, TeamWatchlist.Priority.HIGH)
        self.assertEqual(entry.note, "Death overs option")

        response = self.client.post(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
            data={
                "action": "remove_watchlist",
                "watchlist_entry_id": entry.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
        )
        self.assertFalse(TeamWatchlist.objects.filter(team=self.team, player=target).exists())

    def test_tournament_list_redirects_single_owner_to_dashboard(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("tournament_list"))

        self.assertRedirects(
            response,
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
        )

    def test_owner_dashboard_blocks_unlinked_user(self):
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id})
        )

        self.assertRedirects(response, reverse("tournament_list"))

    def test_owner_can_update_other_player_designation_to_captain(self):
        self.client.force_login(self.owner)
        team_player = TeamPlayer.objects.get(team=self.team, player=self.bought_player)

        response = self.client.post(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
            data={
                "action": "update_owner_squad",
                "team_player_id": team_player.id,
                "designation": TeamPlayer.Designation.CAPTAIN,
            },
        )

        self.assertRedirects(
            response,
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
        )
        team_player.refresh_from_db()
        self.assertEqual(team_player.designation, TeamPlayer.Designation.CAPTAIN)

    def test_owner_cannot_change_own_designation(self):
        self.client.force_login(self.owner)
        own_entry = TeamPlayer.objects.get(team=self.team, player=self.owner_player)

        response = self.client.post(
            reverse("owner_dashboard", kwargs={"slug": self.tournament.slug, "team_id": self.team.id}),
            data={
                "action": "update_owner_squad",
                "team_player_id": own_entry.id,
                "designation": TeamPlayer.Designation.CAPTAIN,
            },
            follow=True,
        )

        own_entry.refresh_from_db()

        self.assertEqual(own_entry.designation, TeamPlayer.Designation.OWNER)
        self.assertContains(response, "cannot change your own designation")
