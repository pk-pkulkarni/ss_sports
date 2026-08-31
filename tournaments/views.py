import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count, Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation

from auctions.models import Auction, Team, TeamPlayer, Player, AuctionLot, Bid
from auctions.rules import (
    AUCTION_TYPE_OPEN,
    AUCTION_TYPE_RULE_BASED,
    RULE_LOT1,
    RULE_LOT1_AND_LOT2,
    RULE_PRICE_CAPS,
    default_auction_rules,
    normalize_auction_rules,
)
from auctions.services import (
    generate_lots_from_active_players,
    generate_random_lots_from_active_players,
    resequence_lot_orders,
)

from .forms import TeamCreateForm, TournamentCreateForm, PlayerForm
from .models import Tournament
from .permissions import can_manage_tournament


def _ensure_auction(tournament: Tournament) -> Auction:
    auction = getattr(tournament, "auction", None)
    if auction:
        return auction

    season = tournament.season_year or ""
    prefix = f"SS-{season}-" if season else "SS-"

    for _ in range(20):
        code = f"{prefix}{get_random_string(6).upper()}"
        try:
            return Auction.objects.create(
                tournament=tournament,
                name="Main Auction",
                code=code,
                status=Auction.Status.DRAFT,
            )
        except IntegrityError:
            continue

    raise RuntimeError("Failed to generate a unique auction code")


def _filter_tournament_qs(qs, user):
    # Accept either a QuerySet or a Model class (get_object_or_404 supports both).
    if hasattr(qs, "_default_manager"):
        qs = qs._default_manager.all()

    if user.is_superuser:
        return qs
    return qs.filter(organizer=user)


def _is_ajax(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _auction_has_activity(auction: Auction | None) -> bool:
    if not auction:
        return False

    if Bid.objects.filter(lot__auction=auction, is_valid=True).exists():
        return True

    return AuctionLot.objects.filter(
        auction=auction,
        status__in=[
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.SOLD,
            AuctionLot.Status.UNSOLD,
        ],
    ).exists()


def _available_lot_count_for_rules(auction: Auction, lot_no: int) -> int:
    assigned_player_ids = (
        TeamPlayer.objects.filter(tournament=auction.tournament)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .values_list("player_id", flat=True)
    )
    return (
        AuctionLot.objects.filter(auction=auction, lot_no=lot_no)
        .exclude(status=AuctionLot.Status.WITHDRAWN)
        .exclude(player_id__in=assigned_player_ids)
        .count()
    )


def _parse_positive_int(raw_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _build_rule_validation_error(auction: Auction, lot_no: int, players_per_team: int) -> str | None:
    active_teams = Team.objects.filter(tournament=auction.tournament, is_active=True).count()
    if active_teams < 1:
        return "Add at least one active team before starting the auction."

    lot_count = _available_lot_count_for_rules(auction, lot_no)
    if lot_count < 1:
        return f"Lot {lot_no} has no auction players available for this rule."

    expected_total = active_teams * players_per_team
    if lot_count != expected_total:
        if lot_count % active_teams == 0:
            exact_value = lot_count // active_teams
            return (
                f"Lot {lot_no} has {lot_count} available players and {active_teams} active teams. "
                f"Use {exact_value} player(s) per team for an exact division."
            )
        return (
            f"Lot {lot_no} has {lot_count} available players and {active_teams} active teams, "
            "so it cannot be divided equally across teams right now."
        )

    return None


@login_required
def tournament_list(request):
    qs = Tournament.objects.select_related("organizer").order_by("-created_at")
    tournaments = _filter_tournament_qs(qs, request.user)
    if request.method == "POST":
        form = TournamentCreateForm(request.POST)
        if form.is_valid():
            tournament = form.save(commit=False)
            tournament.organizer = request.user
            tournament.save()
            _ensure_auction(tournament)
            messages.success(request, "Tournament created.")
            return redirect("tournament_detail", slug=tournament.slug)
    else:
        form = TournamentCreateForm()

    return render(
        request,
        "tournaments/tournament_list.html",
        {"tournaments": tournaments, "create_form": form},
    )


@login_required
def tournament_create(request):
    if request.method == "POST":
        form = TournamentCreateForm(request.POST)
        if form.is_valid():
            tournament = form.save(commit=False)
            tournament.organizer = request.user
            tournament.save()

            _ensure_auction(tournament)

            messages.success(request, "Tournament created.")
            return redirect("tournament_detail", slug=tournament.slug)
    else:
        form = TournamentCreateForm()

    return render(request, "tournaments/tournament_form.html", {"form": form})


@login_required
def tournament_detail(request, slug: str):
    tournament = get_object_or_404(
        _filter_tournament_qs(
            Tournament.objects.select_related("auction").prefetch_related("teams", "players"),
            request.user,
        ),
        slug=slug,
    )
    teams = tournament.teams.order_by("name")
    players = tournament.players.order_by("name")

    sold_player_ids = set()
    if tournament.auction:
        sold_player_ids = set(
            AuctionLot.objects.filter(
                auction=tournament.auction,
                status=AuctionLot.Status.SOLD,
            ).values_list("player_id", flat=True)
        )

    assigned_player_ids = set(
        TeamPlayer.objects.filter(tournament=tournament).values_list("player_id", flat=True)
    )
    auction = getattr(tournament, "auction", None)
    auction_rules_config = normalize_auction_rules(auction) if auction else default_auction_rules()
    auction_setup_locked = _auction_has_activity(auction)

    context = {
        "tournament": tournament,
        "teams": teams,
        "players": players,
        "sold_player_ids": sold_player_ids,
        "assigned_player_ids": assigned_player_ids,
        "total_players": players.count(),
        "active_players": players.filter(is_active=True).count(),
        "total_teams": teams.count(),
        "can_manage": can_manage_tournament(request.user, tournament),
        "player_form": PlayerForm(),
        "team_form": TeamCreateForm(),
        "auction_rules_config": auction_rules_config,
        "auction_setup_locked": auction_setup_locked,
    }
    return render(request, "tournaments/tournament_detail.html", context)


@login_required
@require_POST
def setup_auction(request, slug: str):
    tournament = get_object_or_404(
        _filter_tournament_qs(Tournament.objects.select_related("auction"), request.user),
        slug=slug,
    )

    if not can_manage_tournament(request.user, tournament):
        messages.error(request, "Forbidden.")
        return redirect("tournament_detail", slug=tournament.slug)

    auction = _ensure_auction(tournament)
    if _auction_has_activity(auction):
        messages.error(request, "Auction rules can't be changed after auction activity has started.")
        return redirect("auction_screen", slug=tournament.slug)

    auction_type = (request.POST.get("auction_type") or AUCTION_TYPE_OPEN).strip().lower()
    if auction_type not in {AUCTION_TYPE_OPEN, AUCTION_TYPE_RULE_BASED}:
        auction_type = AUCTION_TYPE_OPEN

    if auction_type == AUCTION_TYPE_OPEN:
        auction.rules = {"auction_type": AUCTION_TYPE_OPEN}
        auction.save(update_fields=["rules", "updated_at"])
        messages.success(request, "Auction is set to open auction.")
        return redirect("auction_screen", slug=tournament.slug)

    rule_lot1 = request.POST.get("rule_lot1") == "1"
    rule_lot1_and_lot2 = request.POST.get("rule_lot1_and_lot2") == "1"
    rule_price_caps = request.POST.get("rule_price_caps") == "1"

    if rule_lot1 and rule_lot1_and_lot2:
        messages.error(request, "Select either Rule 1 or Rule 2, not both.")
        return redirect("tournament_detail", slug=tournament.slug)

    if not any([rule_lot1, rule_lot1_and_lot2, rule_price_caps]):
        messages.error(request, "Select at least one rule for a rule-based auction.")
        return redirect("tournament_detail", slug=tournament.slug)

    lot1_players_per_team = None
    lot2_players_per_team = None

    if rule_lot1:
        lot1_players_per_team = _parse_positive_int(request.POST.get("rule1_lot1_players_per_team"))
        if not lot1_players_per_team:
            messages.error(request, "Enter a valid Lot 1 players-per-team value for Rule 1.")
            return redirect("tournament_detail", slug=tournament.slug)

        error = _build_rule_validation_error(auction, 1, lot1_players_per_team)
        if error:
            messages.error(request, error)
            return redirect("tournament_detail", slug=tournament.slug)

    if rule_lot1_and_lot2:
        lot1_players_per_team = _parse_positive_int(request.POST.get("rule2_lot1_players_per_team"))
        lot2_players_per_team = _parse_positive_int(request.POST.get("rule2_lot2_players_per_team"))

        if not lot1_players_per_team or not lot2_players_per_team:
            messages.error(request, "Enter valid Lot 1 and Lot 2 players-per-team values for Rule 2.")
            return redirect("tournament_detail", slug=tournament.slug)

        for lot_no, per_team in ((1, lot1_players_per_team), (2, lot2_players_per_team)):
            error = _build_rule_validation_error(auction, lot_no, per_team)
            if error:
                messages.error(request, error)
                return redirect("tournament_detail", slug=tournament.slug)

    auction.rules = {
        "auction_type": AUCTION_TYPE_RULE_BASED,
        RULE_LOT1: rule_lot1,
        RULE_LOT1_AND_LOT2: rule_lot1_and_lot2,
        RULE_PRICE_CAPS: rule_price_caps,
        "lot1_players_per_team": lot1_players_per_team,
        "lot2_players_per_team": lot2_players_per_team,
    }
    auction.save(update_fields=["rules", "updated_at"])

    messages.success(request, "Auction rules saved.")
    return redirect("auction_screen", slug=tournament.slug)


@login_required
@require_POST
def generate_lots(request, slug: str):
    tournament = get_object_or_404(
        _filter_tournament_qs(Tournament.objects.select_related("auction"), request.user),
        slug=slug,
    )

    auction = _ensure_auction(tournament)
    created, skipped = generate_lots_from_active_players(auction)

    messages.success(
        request,
        f"✅ Lots generated. Created: {created}. Skipped (already existed): {skipped}.",
    )
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
@require_POST
def generate_random_lots(request, slug: str):
    tournament = get_object_or_404(
        _filter_tournament_qs(Tournament.objects.select_related("auction"), request.user),
        slug=slug,
    )

    try:
        max_lots = int(request.POST.get("max_lots") or 0)
    except (TypeError, ValueError):
        max_lots = 0

    if max_lots < 2 or max_lots > 5:
        messages.error(request, "Please choose lots between 2 and 5.")
        return redirect("tournament_detail", slug=tournament.slug)

    auction = _ensure_auction(tournament)
    created, skipped = generate_random_lots_from_active_players(auction, max_lots=max_lots, reset=True)
    lot_count = AuctionLot.objects.filter(auction=auction).values("lot_no").distinct().count()

    messages.success(
        request,
        f"✅ Random lots generated (reset). Lots: {lot_count}. Created: {created}. Skipped (already existed): {skipped}.",
    )
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
def team_create(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    if request.method == "POST":
        form = TeamCreateForm(request.POST, request.FILES)
        if form.is_valid():
            team = form.save(commit=False)
            team.tournament = tournament

            if not team.short_name:
                team.short_name = "".join([w[0] for w in team.name.split()]).upper()[:8]

            # Remaining defaults to total (handled in the form), but keep a final safety net.
            if team.purse_remaining is None:
                team.purse_remaining = team.purse_total or 0

            team.save()

            if _is_ajax(request):
                team_html = render_to_string(
                    "tournaments/partials/team_card.html",
                    {"team": team, "tournament": tournament},
                    request=request,
                )
                players_qs = tournament.players.all()
                teams_qs = tournament.teams.all()
                return JsonResponse(
                    {
                        "ok": True,
                        "team": {"id": team.id, "name": team.name},
                        "team_html": team_html,
                        "counts": {
                            "total_teams": teams_qs.count(),
                            "total_players": players_qs.count(),
                            "active_players": players_qs.filter(is_active=True).count(),
                        },
                    }
                )

            messages.success(request, "Team created.")
            return redirect("tournament_detail", slug=tournament.slug)

        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Could not create team. Please check the inputs.",
                    "errors": form.errors.get_json_data(),
                },
                status=400,
            )
    else:
        form = TeamCreateForm()

    return render(
        request,
        "tournaments/team_form.html",
        {"tournament": tournament, "form": form},
    )


@login_required
def team_edit(request, slug: str, team_id: int):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    if not can_manage_tournament(request.user, tournament):
        messages.error(request, "Forbidden.")
        return redirect("tournament_detail", slug=tournament.slug)

    team = get_object_or_404(Team, pk=team_id, tournament=tournament)

    action = (request.POST.get("action") or "").strip() if request.method == "POST" else ""

    # Team details save
    if request.method == "POST" and action == "save_team":
        form = TeamCreateForm(request.POST, request.FILES, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, "Team updated.")
            return redirect("team_edit", slug=tournament.slug, team_id=team.id)
        messages.error(request, "Please correct the errors below and try again.")
    else:
        form = TeamCreateForm(instance=team)

    # Update designations
    if request.method == "POST" and action == "update_designations":
        squad_qs = TeamPlayer.objects.filter(tournament=tournament, team=team)
        updated = 0
        with transaction.atomic():
            for tp in squad_qs:
                key = f"designation_{tp.id}"
                new_val = (request.POST.get(key) or "").strip()
                allowed = {c[0] for c in TeamPlayer.Designation.choices}
                if new_val and new_val in allowed and tp.designation != new_val:
                    tp.designation = new_val
                    tp.save(update_fields=["designation", "updated_at"])
                    updated += 1
        messages.success(request, f"Updated designations for {updated} player(s).")
        return redirect("team_edit", slug=tournament.slug, team_id=team.id)

    # Add retained / pre-auction player to team
    if request.method == "POST" and action == "add_player":
        try:
            player_id = int(request.POST.get("player_id") or 0)
        except (TypeError, ValueError):
            player_id = 0

        price_raw = (request.POST.get("retained_price") or "").strip()
        retained_price = Decimal("0")
        if price_raw:
            try:
                retained_price = Decimal(price_raw)
            except (InvalidOperation, ValueError):
                retained_price = Decimal("0")
        if retained_price < 0:
            retained_price = Decimal("0")

        player = get_object_or_404(Player, pk=player_id, tournament=tournament)

        sold_exists = AuctionLot.objects.filter(
            auction__tournament=tournament,
            player=player,
            status=AuctionLot.Status.SOLD,
        ).exists()
        if sold_exists:
            messages.error(request, "Can't assign: this player is already SOLD in auction.")
            return redirect("team_edit", slug=tournament.slug, team_id=team.id)

        if TeamPlayer.objects.filter(tournament=tournament, player=player).exists():
            messages.error(request, "This player is already assigned to a team.")
            return redirect("team_edit", slug=tournament.slug, team_id=team.id)

        auction = _ensure_auction(tournament)
        lot = AuctionLot.objects.filter(auction=auction, player=player).first()
        if lot and lot.status == AuctionLot.Status.RUNNING:
            messages.error(request, "Can't assign while this player's lot is RUNNING.")
            return redirect("team_edit", slug=tournament.slug, team_id=team.id)

        # Purse check (if configured)
        if (team.purse_total or 0) > 0 and retained_price > 0:
            if (team.purse_remaining or 0) < retained_price:
                messages.error(request, "Team doesn't have enough purse remaining for this retained price.")
                return redirect("team_edit", slug=tournament.slug, team_id=team.id)

        with transaction.atomic():
            TeamPlayer.objects.create(
                tournament=tournament,
                team=team,
                player=player,
                bought_in_auction=None,
                bought_price=retained_price,
                status=TeamPlayer.Status.ACTIVE,
                designation=TeamPlayer.Designation.PLAYER,
            )

            # Deduct purse_remaining if configured
            if (team.purse_total or 0) > 0 and retained_price > 0:
                team.purse_remaining = (team.purse_remaining or 0) - retained_price
                team.save(update_fields=["purse_remaining", "updated_at"])

            # Ensure player won't come up for bidding again.
            if lot and lot.status != AuctionLot.Status.SOLD:
                lot.status = AuctionLot.Status.WITHDRAWN
                lot.save(update_fields=["status", "updated_at"])

        messages.success(request, f"Assigned {player.name} to {team.name}.")
        return redirect("team_edit", slug=tournament.slug, team_id=team.id)

    # Remove retained player (only if not sold via auction)
    if request.method == "POST" and action == "remove_player":
        try:
            tp_id = int(request.POST.get("team_player_id") or 0)
        except (TypeError, ValueError):
            tp_id = 0

        tp = get_object_or_404(TeamPlayer, pk=tp_id, tournament=tournament, team=team)
        if tp.bought_in_auction_id:
            messages.error(request, "Can't remove: this player was bought in auction.")
            return redirect("team_edit", slug=tournament.slug, team_id=team.id)

        refund = Decimal(str(tp.bought_price or 0))
        auction = _ensure_auction(tournament)
        lot = AuctionLot.objects.filter(auction=auction, player=tp.player).first()

        with transaction.atomic():
            tp.delete()

            # Refund purse_remaining if configured
            if (team.purse_total or 0) > 0 and refund > 0:
                team.purse_remaining = (team.purse_remaining or 0) + refund
                team.save(update_fields=["purse_remaining", "updated_at"])

            # Optionally reopen lot if it was withdrawn by retention
            if lot and lot.status == AuctionLot.Status.WITHDRAWN:
                lot.status = AuctionLot.Status.PENDING
                lot.save(update_fields=["status", "updated_at"])

        messages.success(request, f"Removed {tp.player.name} from {team.name}.")
        return redirect("team_edit", slug=tournament.slug, team_id=team.id)

    squad = (
        TeamPlayer.objects.filter(tournament=tournament, team=team)
        .select_related("player")
        .order_by("player__name")
    )

    assigned_ids = TeamPlayer.objects.filter(tournament=tournament).values_list("player_id", flat=True)
    sold_ids = AuctionLot.objects.filter(
        auction__tournament=tournament,
        status=AuctionLot.Status.SOLD,
    ).values_list("player_id", flat=True)

    available_players = (
        Player.objects.filter(tournament=tournament)
        .exclude(id__in=assigned_ids)
        .exclude(id__in=sold_ids)
        .order_by("name")
    )

    return render(
        request,
        "tournaments/team_edit.html",
        {
            "tournament": tournament,
            "team": team,
            "form": form,
            "squad": squad,
            "available_players": available_players,
            "designation_choices": TeamPlayer.Designation.choices,
        },
    )


@login_required
def player_create(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    if request.method == "POST":
        form = PlayerForm(request.POST, request.FILES)
        if form.is_valid():
            player = form.save(commit=False)
            player.tournament = tournament
            player.save()

            if _is_ajax(request):
                player_html = render_to_string(
                    "tournaments/partials/player_card.html",
                    {
                        "p": player,
                        "tournament": tournament,
                        "can_manage": can_manage_tournament(request.user, tournament),
                        "sold_player_ids": set(),
                        "assigned_player_ids": set(),
                    },
                    request=request,
                )
                players_qs = tournament.players.all()
                teams_qs = tournament.teams.all()
                return JsonResponse(
                    {
                        "ok": True,
                        "player": {"id": player.id, "name": player.name},
                        "player_html": player_html,
                        "counts": {
                            "total_teams": teams_qs.count(),
                            "total_players": players_qs.count(),
                            "active_players": players_qs.filter(is_active=True).count(),
                        },
                    }
                )

            messages.success(request, "Player created.")
        else:
            if _is_ajax(request):
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Could not create player. Please check the inputs.",
                        "errors": form.errors.get_json_data(),
                    },
                    status=400,
                )
            messages.error(request, "Could not create player. Please check the inputs.")

        return redirect("tournament_detail", slug=tournament.slug)

    # Fallback page (not used in modal flow)
    form = PlayerForm()
    return render(
        request,
        "tournaments/player_form.html",
        {"tournament": tournament, "form": form, "mode": "create"},
    )


@login_required
@require_POST
def player_update(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    try:
        player_id = int(request.POST.get("player_id"))
    except (TypeError, ValueError):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Invalid player."}, status=400)
        messages.error(request, "Invalid player.")
        return redirect("tournament_detail", slug=tournament.slug)

    player = get_object_or_404(Player, pk=player_id, tournament=tournament)
    form = PlayerForm(request.POST, request.FILES, instance=player)
    if form.is_valid():
        form.save()

        if _is_ajax(request):
            sold_player_ids = set(
                AuctionLot.objects.filter(
                    auction__tournament=tournament,
                    player=player,
                    status=AuctionLot.Status.SOLD,
                ).values_list("player_id", flat=True)
            )
            assigned_player_ids = set(
                TeamPlayer.objects.filter(tournament=tournament, player=player).values_list(
                    "player_id", flat=True
                )
            )
            player_html = render_to_string(
                "tournaments/partials/player_card.html",
                {
                    "p": player,
                    "tournament": tournament,
                    "can_manage": can_manage_tournament(request.user, tournament),
                    "sold_player_ids": sold_player_ids,
                    "assigned_player_ids": assigned_player_ids,
                },
                request=request,
            )
            players_qs = tournament.players.all()
            teams_qs = tournament.teams.all()
            return JsonResponse(
                {
                    "ok": True,
                    "player": {"id": player.id, "name": player.name},
                    "player_html": player_html,
                    "counts": {
                        "total_teams": teams_qs.count(),
                        "total_players": players_qs.count(),
                        "active_players": players_qs.filter(is_active=True).count(),
                    },
                }
            )

        messages.success(request, "Player updated.")
    else:
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Could not update player. Please check the inputs.",
                    "errors": form.errors.get_json_data(),
                },
                status=400,
            )
        messages.error(request, "Could not update player. Please check the inputs.")

    return redirect("tournament_detail", slug=tournament.slug)


@login_required
@require_POST
def player_delete(request, slug: str, player_id: int):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    if not can_manage_tournament(request.user, tournament):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
        messages.error(request, "Forbidden.")
        return redirect("tournament_detail", slug=tournament.slug)

    player = get_object_or_404(Player, pk=player_id, tournament=tournament)

    sold_exists = AuctionLot.objects.filter(
        auction__tournament=tournament,
        player=player,
        status=AuctionLot.Status.SOLD,
    ).exists()
    if sold_exists:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Can't delete: this player is SOLD in auction."},
                status=400,
            )
        messages.error(request, "Can't delete: this player is SOLD in auction.")
        return redirect("tournament_detail", slug=tournament.slug)

    if TeamPlayer.objects.filter(tournament=tournament, player=player).exists():
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Can't delete: this player is assigned to a team."},
                status=400,
            )
        messages.error(request, "Can't delete: this player is assigned to a team.")
        return redirect("tournament_detail", slug=tournament.slug)

    running_exists = AuctionLot.objects.filter(
        auction__tournament=tournament,
        player=player,
        status=AuctionLot.Status.RUNNING,
    ).exists()
    if running_exists:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Can't delete: this player's lot is RUNNING."},
                status=400,
            )
        messages.error(request, "Can't delete: this player's lot is RUNNING.")
        return redirect("tournament_detail", slug=tournament.slug)

    player.delete()

    players_qs = tournament.players.all()
    teams_qs = tournament.teams.all()

    if _is_ajax(request):
        return JsonResponse(
            {
                "ok": True,
                "player_id": player_id,
                "counts": {
                    "total_teams": teams_qs.count(),
                    "total_players": players_qs.count(),
                    "active_players": players_qs.filter(is_active=True).count(),
                },
            }
        )

    messages.success(request, "Player deleted.")
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
def player_edit(request, slug: str, player_id: int):
    # Legacy direct-edit page
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)
    player = get_object_or_404(Player, pk=player_id, tournament=tournament)

    if request.method == "POST":
        form = PlayerForm(request.POST, request.FILES, instance=player)
        if form.is_valid():
            form.save()
            messages.success(request, "Player updated.")
            return redirect("tournament_detail", slug=tournament.slug)
    else:
        form = PlayerForm(instance=player)

    return render(
        request,
        "tournaments/player_form.html",
        {"tournament": tournament, "form": form, "player": player, "mode": "edit"},
    )


@login_required
def teams_dashboard(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    teams = (
        Team.objects.filter(tournament=tournament, is_active=True)
        .order_by("name")
    )

    # Load squad for each team (sold players)
    team_players = (
        TeamPlayer.objects.filter(tournament=tournament)
        .select_related("team", "player")
        .order_by("team__name", "player__name")
    )

    # Group players by team_id
    players_by_team = {}
    for tp in team_players:
        players_by_team.setdefault(tp.team_id, []).append(tp)

    # Compute used = sum(bought_price)
    # (purse_total - purse_remaining is also fine, but sum is more trustworthy)
    spent_by_team = (
        TeamPlayer.objects.filter(tournament=tournament)
        .values("team_id")
        .annotate(spent=Sum("bought_price"), count=Count("id"))
    )
    spent_map = {x["team_id"]: (x["spent"] or 0, x["count"] or 0) for x in spent_by_team}

    enriched = []
    for t in teams:
        spent, cnt = spent_map.get(t.id, (0, 0))
        enriched.append({
            "team": t,
            "spent": spent,
            "players_count": cnt,
            "players": players_by_team.get(t.id, []),
        })

    return render(
        request,
        "tournaments/teams_dashboard.html",
        {"tournament": tournament, "teams": enriched},
    )


@login_required
def export_teams_xlsx(request, slug: str):
    """Export all teams into a single XLSX workbook (one sheet per team)."""

    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse(
            "Export requires openpyxl. Install it with: pip install openpyxl",
            status=500,
            content_type="text/plain",
        )

    import re
    from io import BytesIO

    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    teams = list(
        Team.objects.filter(tournament=tournament, is_active=True)
        .order_by("name")
    )

    team_players = (
        TeamPlayer.objects.filter(tournament=tournament)
        .select_related("team", "player")
        .order_by("team__name", "player__name")
    )

    players_by_team = {}
    for tp in team_players:
        players_by_team.setdefault(tp.team_id, []).append(tp)

    wb = Workbook()

    # Remove default sheet
    default_ws = wb.active
    wb.remove(default_ws)

    used_titles = set()

    def safe_sheet_title(name: str) -> str:
        name = (name or "Team").strip() or "Team"
        # Excel sheet title rules: max 31 chars and can't contain: : \\ / ? * [ ]
        name = re.sub(r"[:\\\\/\\?\\*\\[\\]]", " ", name)
        name = re.sub(r"\\s+", " ", name).strip()
        return (name or "Team")[:31]

    for team in teams:
        base_title = safe_sheet_title(team.short_name or team.name)
        title = base_title
        i = 2
        while title in used_titles:
            suffix = f" {i}"
            title = (base_title[: (31 - len(suffix))] + suffix)[:31]
            i += 1
        used_titles.add(title)

        ws = wb.create_sheet(title=title)
        ws.append(["Player Name", "Designation"])

        for tp in players_by_team.get(team.id, []):
            designation = ""
            if tp.designation == TeamPlayer.Designation.OWNER:
                designation = "Owner"
            elif tp.designation == TeamPlayer.Designation.CAPTAIN:
                designation = "Captain"
            ws.append([tp.player.name, designation])

        # Basic formatting (column widths)
        ws.column_dimensions[get_column_letter(1)].width = 32
        ws.column_dimensions[get_column_letter(2)].width = 16
        ws.freeze_panes = "A2"

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"{tournament.slug}_teams.xlsx"
    response = HttpResponse(
        out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_players_xlsx(request, slug: str):
    """Export all tournament players into a single XLSX sheet."""

    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse(
            "Export requires openpyxl. Install it with: pip install openpyxl",
            status=500,
            content_type="text/plain",
        )

    from io import BytesIO

    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)

    players = list(Player.objects.filter(tournament=tournament).order_by("name"))

    auction = getattr(tournament, "auction", None)

    lot_by_player_id = {}
    if auction:
        lots = (
            AuctionLot.objects.filter(auction=auction)
            .select_related("sold_to_team")
            .order_by("lot_no", "lot_order", "id")
        )
        for lot in lots:
            lot_by_player_id[lot.player_id] = lot

    tp_by_player_id = {
        tp.player_id: tp
        for tp in TeamPlayer.objects.filter(tournament=tournament)
        .select_related("team")
        .order_by("player__name")
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "Players"

    headers = [
        "Name",
        # "Role",
        # "City",
        # "Age",
        "Contact",
        # "Active",
        # "Base Price",
        # "Reserve Price",
        # "Team",
        # "Designation",
        # "Lot No",
        # "Lot Status",
        # "Sold To",
        # "Sold Price",
        # "Batting Style",
        # "Bowling Style",
        # "Jersey No",
        # "Jersey Name",
        # "Jersey Size",
        # "Notes",
        # "Stats",
    ]
    ws.append(headers)

    for p in players:
        # lot = lot_by_player_id.get(p.id)
        # tp = tp_by_player_id.get(p.id)
        #
        # stats_val = ""
        # if p.stats:
        #     try:
        #         stats_val = json.dumps(p.stats, ensure_ascii=False)
        #     except Exception:
        #         stats_val = str(p.stats)

        ws.append(
            [
                p.name,
                # p.get_role_display() if p.role else "",
                # p.city or "",
                # p.age or "",
                p.phone or "",
                # "Yes" if p.is_active else "No",
                # p.base_price or 0,
                # p.reserve_price or 0,
                # tp.team.name if tp else "",
                # tp.get_designation_display() if tp else "",
                # lot.lot_no if lot else "",
                # lot.get_status_display() if lot else "",
                # lot.sold_to_team.name if lot and lot.sold_to_team_id else "",
                # lot.sold_price if lot and lot.sold_price is not None else "",
                # p.batting_style or "",
                # p.bowling_style or "",
                # p.jersey_no or "",
                # p.jersey_name or "",
                # p.jersey_size or "",
                # p.notes or "",
                # stats_val,
            ]
        )

    # Basic formatting
    ws.freeze_panes = "A2"
    widths = [
        28,  # Name
        # 14,  # Role
        # 18,  # City
        # 8,   # Age
        # 16,  # Phone
        # 8,   # Active
        # 12,  # Base
        # 12,  # Reserve
        # 20,  # Team
        # 14,  # Designation
        # 8,   # Lot No
        # 14,  # Lot Status
        # 20,  # Sold To
        # 12,  # Sold Price
        # 16,  # Batting
        # 16,  # Bowling
        # 10,  # Jersey No
        # 14,  # Jersey Name
        # 12,  # Jersey Size
        # 26,  # Notes
        # 44,  # Stats
    ]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"{tournament.slug}_players.xlsx"
    response = HttpResponse(
        out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def lot_manager(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)
    auction = _ensure_auction(tournament)

    lots = (
        AuctionLot.objects.filter(auction=auction)
        .select_related("player")
        .order_by("lot_no", "lot_order", "id")
    )

    lots_by_no = {}
    for lot in lots:
        lots_by_no.setdefault(lot.lot_no, []).append(lot)

    max_lot_no = max([4] + list(lots_by_no.keys())) if lots_by_no else 4
    lot_numbers = list(range(1, max_lot_no + 1))

    lot_columns = [{"lot_no": n, "lots": lots_by_no.get(n, [])} for n in lot_numbers]

    return render(
        request,
        "tournaments/lot_manager.html",
        {
            "tournament": tournament,
            "auction": auction,
            "lot_columns": lot_columns,
        },
    )


@login_required
@require_POST
def move_lot_player(request, slug: str):
    tournament = get_object_or_404(_filter_tournament_qs(Tournament, request.user), slug=slug)
    auction = _ensure_auction(tournament)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        lot_id = int(payload.get("lot_id"))
        target_lot_no = int(payload.get("target_lot_no"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    if target_lot_no < 1:
        return JsonResponse({"ok": False, "error": "Invalid target lot."}, status=400)

    lot = get_object_or_404(AuctionLot, pk=lot_id, auction=auction)
    if lot.status != AuctionLot.Status.PENDING:
        return JsonResponse({"ok": False, "error": "Only pending lots can be moved."}, status=400)

    source_lot_no = lot.lot_no

    with transaction.atomic():
        max_order = (
            AuctionLot.objects.filter(auction=auction, lot_no=target_lot_no)
            .aggregate(Max("lot_order"))
            .get("lot_order__max")
            or 0
        )
        lot.lot_no = target_lot_no
        lot.lot_order = max_order + 1
        lot.save(update_fields=["lot_no", "lot_order", "updated_at"])

        resequence_lot_orders(auction, source_lot_no)
        resequence_lot_orders(auction, target_lot_no)

    return JsonResponse(
        {
            "ok": True,
            "lot_id": lot.id,
            "lot_no": lot.lot_no,
            "lot_order": lot.lot_order,
        }
    )
