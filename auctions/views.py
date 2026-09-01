import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction
from django.db.models import Q, Count

from tournaments.models import Tournament
from tournaments.permissions import can_manage_tournament
from .models import AuctionLot, Bid, Team, TeamPlayer, Auction, AuctionEvent
from .rules import PRICE_CAP_VALUES, normalize_auction_rules
from .templatetags.inr import inr as inr_format


# ------------------ Increment Rules ------------------

def _allowed_next_amount(current: Decimal) -> Decimal:
    """
    Enforce your rules:
      < 1L        -> +10k
      1L to < 4L  -> +20k
      >= 4L       -> +50k

    Notes:
      - current is the current highest amount (or base snapshot if no bids)
      - returns the ONLY allowed next amount (MVP strict)
    """
    ten_k = Decimal("10000")
    one_l = Decimal("100000")
    four_l = Decimal("400000")

    if current < one_l:
        return current + ten_k

    if one_l <= current < four_l:
        return current + Decimal("20000")

    # after 4L, +50k
    if current >= four_l:
        return current + Decimal("50000")

    # fallback
    return current + Decimal("20000")


def _get_current_highest(lot: AuctionLot) -> Decimal:
    """
    Highest valid bid amount, else base_price_snapshot.
    """
    top = (
        Bid.objects.filter(lot=lot, is_valid=True)
        .order_by("-amount", "-bid_time")
        .values_list("amount", flat=True)
        .first()
    )
    if top is None:
        return Decimal(lot.base_price_snapshot or 0)
    return Decimal(top)


def _get_highest_team(lot: AuctionLot):
    top = (
        Bid.objects.filter(lot=lot, is_valid=True)
        .order_by("-amount", "-bid_time")
        .select_related("team")
        .first()
    )
    return top.team if top else None


def _get_highest_sold_lot(auction: Auction):
    return (
        AuctionLot.objects.filter(
            auction=auction,
            status=AuctionLot.Status.SOLD,
            sold_price__isnull=False,
        )
        .select_related("player", "sold_to_team")
        .order_by("-sold_price", "lot_no")
        .first()
    )


def _assigned_player_ids_qs(tournament: Tournament):
    return (
        TeamPlayer.objects.filter(tournament=tournament)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .values_list("player_id", flat=True)
    )


def _withdraw_open_lots_for_assigned_players(auction: Auction) -> int:
    """If a player is already assigned to a team, they should not be auctioned."""

    assigned_player_ids = _assigned_player_ids_qs(auction.tournament)

    return AuctionLot.objects.filter(
        auction=auction,
        player_id__in=assigned_player_ids,
    ).exclude(
        status__in=[AuctionLot.Status.SOLD, AuctionLot.Status.WITHDRAWN]
    ).update(status=AuctionLot.Status.WITHDRAWN)


def _auto_complete_auction_if_done(auction):
    # Keep data consistent: players assigned to teams must not remain open in auction.
    _withdraw_open_lots_for_assigned_players(auction)

    open_exists = AuctionLot.objects.filter(
        auction=auction,
        status__in=[AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING],
    ).exists()

    if not open_exists:
        if auction.status != Auction.Status.CLOSED:
            auction.status = Auction.Status.CLOSED
            auction.save(update_fields=["status", "updated_at"])
        return True
    return False


def _lot_rule_player_count(team: Team, auction: Auction, lot_no: int) -> int:
    return (
        TeamPlayer.objects.filter(
            tournament=auction.tournament,
            team=team,
            player__lots__auction=auction,
            player__lots__lot_no=lot_no,
        )
        .exclude(status=TeamPlayer.Status.RELEASED)
        .distinct()
        .count()
    )


def _price_cap_player_count(team: Team, auction: Auction, base_price: Decimal) -> int:
    return (
        TeamPlayer.objects.filter(
            tournament=auction.tournament,
            team=team,
            player__base_price=base_price,
        )
        .exclude(status=TeamPlayer.Status.RELEASED)
        .count()
    )


def _restricted_lot_is_incomplete(auction: Auction, lot_no: int) -> bool:
    assigned_player_ids = _assigned_player_ids_qs(auction.tournament)
    return (
        AuctionLot.objects.filter(
            auction=auction,
            lot_no=lot_no,
            status__in=[
                AuctionLot.Status.PENDING,
                AuctionLot.Status.RUNNING,
                AuctionLot.Status.UNSOLD,
            ],
        )
        .exclude(player_id__in=assigned_player_ids)
        .exists()
    )


def _relist_unsold_lot_if_needed(auction: Auction, lot_no: int) -> int:
    assigned_player_ids = _assigned_player_ids_qs(auction.tournament)
    lot_open_exists = AuctionLot.objects.filter(
        auction=auction,
        lot_no=lot_no,
        status__in=[AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING],
    ).exclude(player_id__in=assigned_player_ids).exists()
    if lot_open_exists:
        return 0

    lot_unsold_ids = list(
        AuctionLot.objects.filter(
            auction=auction,
            lot_no=lot_no,
            status=AuctionLot.Status.UNSOLD,
        ).exclude(player_id__in=assigned_player_ids).values_list("id", flat=True)
    )
    if not lot_unsold_ids:
        return 0

    AuctionLot.objects.filter(id__in=lot_unsold_ids).update(status=AuctionLot.Status.PENDING)
    Bid.objects.filter(lot_id__in=lot_unsold_ids, is_valid=True).update(is_valid=False)

    if auction.status == Auction.Status.CLOSED:
        auction.status = Auction.Status.LIVE
        auction.save(update_fields=["status", "updated_at"])

    return len(lot_unsold_ids)


def _active_restricted_lot(auction: Auction):
    rules = normalize_auction_rules(auction)
    if not rules["is_rule_based"]:
        return None, rules

    lot_sequence = []
    if rules["rule_lot1_and_lot2_enabled"]:
        lot_sequence = [1, 2]
    elif rules["rule_lot1_enabled"]:
        lot_sequence = [1]

    for lot_no in lot_sequence:
        if _restricted_lot_is_incomplete(auction, lot_no):
            return lot_no, rules

    return None, rules


def _relist_restricted_unsold_if_needed(auction: Auction) -> int:
    lot_no, _rules = _active_restricted_lot(auction)
    if not lot_no:
        return 0
    return _relist_unsold_lot_if_needed(auction, lot_no)


def _lot_rule_limit_for_current_lot(auction: Auction, lot: AuctionLot, rules: dict) -> int | None:
    if lot.lot_no == 1 and (rules["rule_lot1_enabled"] or rules["rule_lot1_and_lot2_enabled"]):
        return rules["lot1_players_per_team"]
    if lot.lot_no == 2 and rules["rule_lot1_and_lot2_enabled"]:
        return rules["lot2_players_per_team"]
    return None


def _validate_team_against_rules(auction: Auction, lot: AuctionLot, team: Team, rules: dict) -> str | None:
    lot_limit = _lot_rule_limit_for_current_lot(auction, lot, rules)
    if lot_limit:
        lot_count = _lot_rule_player_count(team, auction, lot.lot_no)
        if lot_count >= lot_limit:
            return f"{team.name} already has the allowed {lot_limit} player(s) from Lot {lot.lot_no}."

    if rules["rule_price_caps_enabled"] and int(lot.base_price_snapshot or 0) in PRICE_CAP_VALUES:
        base_price = Decimal(lot.base_price_snapshot or 0)
        cap_count = _price_cap_player_count(team, auction, base_price)
        if cap_count >= 1:
            return f"{team.name} already has a {int(base_price)} value player."

    return None


def _tournament_for_user(user, slug: str):
    qs = Tournament.objects.select_related("auction").prefetch_related("teams", "players")
    if not user.is_superuser:
        qs = qs.filter(organizer=user)
    return get_object_or_404(qs, slug=slug)


def _appearance_no(lot: AuctionLot) -> int:
    before = AuctionLot.objects.filter(auction=lot.auction).filter(
        Q(lot_no__lt=lot.lot_no)
        | Q(lot_no=lot.lot_no, lot_order__lt=lot.lot_order)
        | Q(lot_no=lot.lot_no, lot_order=lot.lot_order, id__lt=lot.id)
    )
    return before.count() + 1


def _current_lot_for_auction(auction: Auction):
    # Keep auction consistent with team assignments before selecting the live lot.
    _withdraw_open_lots_for_assigned_players(auction)
    _relist_restricted_unsold_if_needed(auction)
    restricted_lot_no, auction_rules_config = _active_restricted_lot(auction)

    lot_scope = AuctionLot.objects.filter(auction=auction)
    if restricted_lot_no:
        lot_scope = lot_scope.filter(lot_no=restricted_lot_no)

    current_lot = (
        lot_scope.filter(status=AuctionLot.Status.RUNNING)
        .select_related("player", "sold_to_team")
        .order_by("lot_no", "lot_order", "id")
        .first()
    )
    if not current_lot:
        current_lot = (
            lot_scope.filter(status=AuctionLot.Status.PENDING)
            .select_related("player", "sold_to_team")
            .order_by("lot_no", "lot_order", "id")
            .first()
        )
    if not current_lot:
        current_lot = (
            lot_scope.select_related("player", "sold_to_team")
            .order_by("lot_no", "lot_order", "id")
            .first()
        )

    if current_lot and current_lot.status in [AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING]:
        _ensure_lot_start_event(current_lot)

    return current_lot, auction_rules_config


def _auction_completion_summary(auction: Auction) -> dict:
    open_exists = AuctionLot.objects.filter(
        auction=auction,
        status__in=[AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING],
    ).exists()
    auction_fully_completed = not AuctionLot.objects.filter(
        auction=auction,
        status__in=[
            AuctionLot.Status.PENDING,
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.UNSOLD,
        ],
    ).exists()

    sold_total = AuctionLot.objects.filter(auction=auction, status=AuctionLot.Status.SOLD).count()
    unsold_total = AuctionLot.objects.filter(
        auction=auction,
        status__in=[
            AuctionLot.Status.PENDING,
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.UNSOLD,
        ],
    ).count()

    top_sold = _get_highest_sold_lot(auction)
    highest_sold = None
    if top_sold:
        highest_sold = {
            "lot_id": top_sold.id,
            "lot_no": top_sold.lot_no,
            "player": top_sold.player.name,
            "team": top_sold.sold_to_team.name if top_sold.sold_to_team else "",
            "price": str(top_sold.sold_price or 0),
            "price_display": _format_amount(top_sold.sold_price or 0),
        }

    return {
        "auction_completed": not open_exists,
        "auction_fully_completed": auction_fully_completed,
        "sold_total": sold_total,
        "unsold_total": unsold_total,
        "highest_sold": highest_sold,
    }


def _serialize_auction_event(event: AuctionEvent) -> dict:
    return {
        "id": event.id,
        "message": event.message,
        "level": event.level,
        "event_type": event.event_type,
        "time": event.created_at.strftime("%H:%M:%S"),
        "player": event.player.name if event.player_id else "",
        "team": event.team.name if event.team_id else "",
        "lot_no": event.lot.lot_no if event.lot_id else None,
        "amount": str(event.amount or 0),
        "amount_display": _format_amount(event.amount or 0) if event.amount is not None else "",
    }


def _serialize_stage_team(team: Team, squad_count: int, latest_sale: AuctionEvent | None) -> dict:
    spent = Decimal(team.purse_total or 0) - Decimal(team.purse_remaining or 0)
    slots_remaining = ""
    if team.max_players:
        slots_remaining = max(team.max_players - squad_count, 0)

    last_buy = None
    if latest_sale:
        last_buy = {
            "player": latest_sale.player.name if latest_sale.player_id else "",
            "price": str(latest_sale.amount or 0),
            "price_display": _format_amount(latest_sale.amount or 0),
            "time": latest_sale.created_at.strftime("%H:%M:%S"),
        }

    return {
        "id": team.id,
        "name": team.name,
        "short_name": team.short_name or team.name[:3].upper(),
        "tagline": team.tagline or "",
        "logo": team.logo.url if team.logo else "",
        "primary_color": team.primary_color,
        "secondary_color": team.secondary_color,
        "accent_color": team.accent_color,
        "purse_total": str(team.purse_total or 0),
        "purse_total_display": _format_amount(team.purse_total or 0),
        "purse_remaining": str(team.purse_remaining or 0),
        "purse_remaining_display": _format_amount(team.purse_remaining or 0),
        "spent": str(spent),
        "spent_display": _format_amount(spent),
        "squad_count": squad_count,
        "max_players": team.max_players or "",
        "slots_remaining": slots_remaining,
        "last_buy": last_buy,
    }


def _serialize_stage_lot(lot: AuctionLot | None) -> dict | None:
    if not lot:
        return None

    current_bid = _get_current_highest(lot)
    leading_team = _get_highest_team(lot)

    team_payload = None
    if leading_team:
        team_payload = {
            "id": leading_team.id,
            "name": leading_team.name,
            "short_name": leading_team.short_name or leading_team.name[:3].upper(),
            "logo": leading_team.logo.url if leading_team.logo else "",
            "primary_color": leading_team.primary_color,
            "secondary_color": leading_team.secondary_color,
            "accent_color": leading_team.accent_color,
        }

    return {
        "id": lot.id,
        "lot_no": lot.lot_no,
        "appearance_no": _appearance_no(lot),
        "status": lot.status,
        "base_price": str(lot.base_price_snapshot or 0),
        "base_price_display": _format_amount(lot.base_price_snapshot or 0),
        "current_bid": str(current_bid or 0),
        "current_bid_display": _format_amount(current_bid or 0),
        "leading_team": team_payload,
        "player": {
            "id": lot.player_id,
            "name": lot.player.name,
            "photo": lot.player.photo.url if lot.player.photo else "",
            "role": lot.player.get_role_display() if lot.player.role else "",
            "city": lot.player.city or "",
            "age": lot.player.age or "",
            "batting_style": lot.player.batting_style or "",
            "bowling_style": lot.player.bowling_style or "",
            "stats": lot.player.stats or {},
        },
    }


def _latest_sale_payload(auction: Auction) -> dict | None:
    latest_sale = (
        AuctionEvent.objects.filter(
            auction=auction,
            event_type=AuctionEvent.EventType.SOLD,
        )
        .select_related("lot", "player", "team")
        .order_by("-id")
        .first()
    )
    if not latest_sale:
        return None

    return {
        "id": latest_sale.id,
        "player": latest_sale.player.name if latest_sale.player_id else "",
        "team": latest_sale.team.name if latest_sale.team_id else "",
        "team_logo": latest_sale.team.logo.url if latest_sale.team_id and latest_sale.team.logo else "",
        "primary_color": latest_sale.team.primary_color if latest_sale.team_id else "#0F172A",
        "secondary_color": latest_sale.team.secondary_color if latest_sale.team_id else "#22C55E",
        "accent_color": latest_sale.team.accent_color if latest_sale.team_id else "#F97316",
        "lot_no": latest_sale.lot.lot_no if latest_sale.lot_id else None,
        "price": str(latest_sale.amount or 0),
        "price_display": _format_amount(latest_sale.amount or 0),
        "time": latest_sale.created_at.strftime("%H:%M:%S"),
    }


def _stage_leaderboard(auction: Auction) -> list[dict]:
    teams = list(auction.tournament.teams.filter(is_active=True).order_by("name"))
    squad_count_map = {
        row["team_id"]: row["count"]
        for row in TeamPlayer.objects.filter(
            tournament=auction.tournament,
            status=TeamPlayer.Status.ACTIVE,
        )
        .values("team_id")
        .annotate(count=Count("id"))
    }

    latest_sales_by_team = {}
    for event in (
            AuctionEvent.objects.filter(
                auction=auction,
                event_type=AuctionEvent.EventType.SOLD,
                team__isnull=False,
            )
                    .select_related("player", "team")
                    .order_by("-id")[:200]
    ):
        if event.team_id not in latest_sales_by_team:
            latest_sales_by_team[event.team_id] = event
        if len(latest_sales_by_team) == len(teams):
            break

    teams.sort(
        key=lambda team: (
            -(squad_count_map.get(team.id, 0)),
            Decimal(team.purse_remaining or 0),
            team.name.lower(),
        )
    )

    return [
        _serialize_stage_team(
            team=team,
            squad_count=squad_count_map.get(team.id, 0),
            latest_sale=latest_sales_by_team.get(team.id),
        )
        for team in teams
    ]


def _public_auction_snapshot(auction: Auction) -> dict:
    current_lot, _auction_rules_config = _current_lot_for_auction(auction)
    completion = _auction_completion_summary(auction)
    return {
        "auction_id": auction.id,
        "auction_name": auction.name,
        "auction_code": auction.code,
        "status": auction.status,
        "status_display": auction.get_status_display(),
        "banner": auction.banner.url if auction.banner else "",
        "current_lot": _serialize_stage_lot(current_lot),
        "latest_sale": _latest_sale_payload(auction),
        "highest_sold": completion["highest_sold"],
        "sold_total": completion["sold_total"],
        "unsold_total": completion["unsold_total"],
        "auction_completed": completion["auction_completed"],
        "auction_fully_completed": completion["auction_fully_completed"],
        "leaderboard": _stage_leaderboard(auction),
    }


def _format_amount(amount) -> str:
    if amount is None:
        return ""
    return f"₹ {inr_format(amount)}"


def _log_event(
        *,
        auction: Auction,
        event_type: str,
        level: str,
        message: str,
        lot: AuctionLot | None = None,
        team: Team | None = None,
        player=None,
        amount=None,
):
    return AuctionEvent.objects.create(
        auction=auction,
        event_type=event_type,
        level=level,
        message=message,
        lot=lot,
        team=team,
        player=player,
        amount=amount,
    )


def _ensure_lot_start_event(lot: AuctionLot) -> None:
    exists = AuctionEvent.objects.filter(
        auction=lot.auction,
        lot=lot,
        event_type=AuctionEvent.EventType.LOT_START,
    ).exists()
    if not exists:
        msg = f"{lot.player.name} is up for auction (Lot #{lot.lot_no})."
        _log_event(
            auction=lot.auction,
            event_type=AuctionEvent.EventType.LOT_START,
            level=AuctionEvent.Level.INFO,
            message=msg,
            lot=lot,
            player=lot.player,
        )


def _maybe_log_milestone(lot: AuctionLot, amount: Decimal) -> None:
    if amount < Decimal("100000"):
        return
    exists = AuctionEvent.objects.filter(
        auction=lot.auction,
        lot=lot,
        event_type=AuctionEvent.EventType.MILESTONE_1L,
    ).exists()
    if not exists:
        msg = f"🎉 {lot.player.name} crossed {_format_amount(amount)}!"
        _log_event(
            auction=lot.auction,
            event_type=AuctionEvent.EventType.MILESTONE_1L,
            level=AuctionEvent.Level.MILESTONE,
            message=msg,
            lot=lot,
            player=lot.player,
            amount=amount,
        )


# ------------------ Views ------------------

@login_required
def auction_screen(request, slug: str):
    tournament = _tournament_for_user(request.user, slug)
    auction = getattr(tournament, "auction", None)
    teams_qs = tournament.teams.filter(is_active=True).order_by("name")

    count_map = {
        x["team_id"]: x["count"]
        for x in TeamPlayer.objects.filter(tournament=tournament, status=TeamPlayer.Status.ACTIVE)
        .values("team_id")
        .annotate(count=Count("id"))
    }

    teams = list(teams_qs)
    for t in teams:
        t.players_count = count_map.get(t.id, 0)

    current_lot = None
    highest_sold = None
    auction_completed = False
    auction_fully_completed = False
    sold_total = 0
    unsold_total = 0

    if auction:
        current_lot, auction_rules_config = _current_lot_for_auction(auction)
        completion = _auction_completion_summary(auction)
        auction_completed = completion["auction_completed"]
        auction_fully_completed = completion["auction_fully_completed"]
        sold_total = completion["sold_total"]
        unsold_total = completion["unsold_total"]
        if completion["highest_sold"]:
            highest_sold = {
                "player": completion["highest_sold"]["player"],
                "team": completion["highest_sold"]["team"],
                "price": Decimal(completion["highest_sold"]["price"] or 0),
                "lot_no": completion["highest_sold"]["lot_no"],
            }
    else:
        auction_rules_config = normalize_auction_rules(None)

    bid_timer_seconds = int(getattr(settings, "BID_TIMER_IN_SECONDS", 0) or 0)
    appearance_no = _appearance_no(current_lot) if current_lot else None

    return render(
        request,
        "auctions/auction_screen.html",
        {
            "tournament": tournament,
            "auction": auction,
            "teams": teams,
            "current_lot": current_lot,
            "appearance_no": appearance_no,
            "highest_sold": highest_sold,
            "bid_timer_seconds": bid_timer_seconds,
            "auction_completed": auction_completed,
            "auction_fully_completed": auction_fully_completed,
            "sold_total": sold_total,
            "unsold_total": unsold_total,
            "auction_rules_config": auction_rules_config,
        },
    )


@login_required
@require_POST
@csrf_exempt
def place_bid(request, lot_id: int):
    """
    Body JSON:
      { "team_id": <int> }
    Server computes the next amount using rules, and saves Bid.
    """
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    if lot.status not in [AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING]:
        return JsonResponse({"ok": False, "error": "Lot is not open for bidding."}, status=400)

    # Safety: if player is already assigned to a team (retained), withdraw lot and block bidding.
    assigned_exists = (
        TeamPlayer.objects.filter(tournament=lot.auction.tournament, player_id=lot.player_id)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .exists()
    )
    if assigned_exists:
        AuctionLot.objects.filter(pk=lot.pk).exclude(
            status__in=[AuctionLot.Status.SOLD, AuctionLot.Status.WITHDRAWN]
        ).update(status=AuctionLot.Status.WITHDRAWN)
        return JsonResponse({"ok": False, "error": "Player is already assigned to a team."}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        team_id = int(payload.get("team_id"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    team = get_object_or_404(Team, pk=team_id, tournament=lot.auction.tournament, is_active=True)
    rules = normalize_auction_rules(lot.auction)
    rule_error = _validate_team_against_rules(lot.auction, lot, team, rules)
    if rule_error:
        return JsonResponse({"ok": False, "error": rule_error}, status=400)

    with transaction.atomic():
        # First bid should be exactly the base price.
        top_amount = (
            Bid.objects.filter(lot=lot, is_valid=True)
            .order_by("-amount", "-bid_time")
            .values_list("amount", flat=True)
            .first()
        )

        if top_amount is None:
            next_amount = Decimal(lot.base_price_snapshot or 0)
        else:
            current = Decimal(top_amount)
            next_amount = _allowed_next_amount(current)

        # OPTIONAL (strict): ensure team has enough purse_remaining if set
        # If purse_total is 0, we treat it as not configured yet and allow bidding.
        if (team.purse_total or 0) > 0:
            purse_remaining = Decimal(team.purse_remaining or 0)

            # Enforce max_players / reserve rule (assume every remaining player costs at least 10k).
            if team.max_players:
                players_in_team = TeamPlayer.objects.filter(
                    tournament=lot.auction.tournament,
                    team=team,
                    status=TeamPlayer.Status.ACTIVE,
                ).count()

                if players_in_team >= team.max_players:
                    return JsonResponse(
                        {"ok": False, "error": f"{team.name} already has max players."},
                        status=400,
                    )

                remaining_after_purchase = max(team.max_players - (players_in_team + 1), 0)
                min_per_player = Decimal("10000")
                reserve_required = min_per_player * Decimal(remaining_after_purchase)

                required_total = next_amount + reserve_required
                if purse_remaining < required_total:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": (
                                f"{team.name} needs at least {_format_amount(required_total)} purse to place this bid "
                                f"({_format_amount(next_amount)} bid + {_format_amount(reserve_required)} reserved for {remaining_after_purchase} remaining players)."
                            ),
                        },
                        status=400,
                    )

            # Basic purse check
            if purse_remaining < next_amount:
                return JsonResponse(
                    {"ok": False, "error": f"{team.name} doesn't have enough purse for ₹{next_amount}."},
                    status=400,
                )

        bid = Bid.objects.create(lot=lot, team=team, amount=next_amount, is_valid=True)

        # Mark lot as RUNNING when first bid comes in (optional but nice)
        if lot.status == AuctionLot.Status.PENDING:
            lot.status = AuctionLot.Status.RUNNING
            lot.save(update_fields=["status", "updated_at"])

        _ensure_lot_start_event(lot)
        _log_event(
            auction=lot.auction,
            event_type=AuctionEvent.EventType.BID,
            level=AuctionEvent.Level.BID,
            message=f"{team.name} bid {_format_amount(next_amount)}",
            lot=lot,
            player=lot.player,
            team=team,
            amount=next_amount,
        )
        _maybe_log_milestone(lot, next_amount)

    return JsonResponse(
        {
            "ok": True,
            "lot_id": lot.id,
            "team": {"id": team.id, "name": team.name},
            "amount": str(next_amount),
            "bid_id": bid.id,
        }
    )


@login_required
@require_POST
@csrf_exempt
def mark_sold(request, lot_id: int):
    """
    Marks lot SOLD to highest bidder:
      - sets sold_to_team, sold_price, status
      - creates TeamPlayer
      - updates team purse_remaining
    """
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament", "player"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    if lot.status in [AuctionLot.Status.SOLD, AuctionLot.Status.UNSOLD, AuctionLot.Status.WITHDRAWN]:
        return JsonResponse({"ok": False, "error": "Lot already closed."}, status=400)

    # Safety: don't allow selling a player that is already assigned/retained.
    assigned_exists = (
        TeamPlayer.objects.filter(tournament=lot.auction.tournament, player_id=lot.player_id)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .exists()
    )
    if assigned_exists:
        AuctionLot.objects.filter(pk=lot.pk).exclude(
            status__in=[AuctionLot.Status.SOLD, AuctionLot.Status.WITHDRAWN]
        ).update(status=AuctionLot.Status.WITHDRAWN)
        return JsonResponse({"ok": False, "error": "Player is already assigned to a team."}, status=400)

    with transaction.atomic():
        team = _get_highest_team(lot)
        if not team:
            return JsonResponse({"ok": False, "error": "No bids found. Can't mark SOLD."}, status=400)

        sold_price = _get_current_highest(lot)
        rules = normalize_auction_rules(lot.auction)
        rule_error = _validate_team_against_rules(lot.auction, lot, team, rules)
        if rule_error:
            return JsonResponse({"ok": False, "error": rule_error}, status=400)

        # purse check (if configured)
        if (team.purse_total or 0) > 0 and (team.purse_remaining or 0) < sold_price:
            return JsonResponse({"ok": False, "error": f"{team.name} doesn't have purse for ₹{sold_price}."},
                                status=400)

        # Update lot
        lot.status = AuctionLot.Status.SOLD
        lot.sold_to_team = team
        lot.sold_price = sold_price
        lot.save(update_fields=["status", "sold_to_team", "sold_price", "updated_at"])

        # Add to squad (unique per tournament player)
        TeamPlayer.objects.update_or_create(
            tournament=lot.auction.tournament,
            player=lot.player,
            defaults={
                "team": team,
                "bought_in_auction": lot.auction,
                "bought_price": sold_price,
                "status": TeamPlayer.Status.ACTIVE,
            },
        )

        # Deduct purse_remaining (only if configured)
        if (team.purse_total or 0) > 0:
            team.purse_remaining = (team.purse_remaining or 0) - sold_price
            team.save(update_fields=["purse_remaining", "updated_at"])
        _log_event(
            auction=lot.auction,
            event_type=AuctionEvent.EventType.SOLD,
            level=AuctionEvent.Level.SOLD,
            message=f"SOLD! {lot.player.name} to {team.name} for {_format_amount(sold_price)}",
            lot=lot,
            player=lot.player,
            team=team,
            amount=sold_price,
        )
        _relist_restricted_unsold_if_needed(lot.auction)

        done = _auto_complete_auction_if_done(lot.auction)
        fully_done = not AuctionLot.objects.filter(
            auction=lot.auction,
            status__in=[
                AuctionLot.Status.PENDING,
                AuctionLot.Status.RUNNING,
                AuctionLot.Status.UNSOLD,
            ],
        ).exists()
        top_sold = _get_highest_sold_lot(lot.auction)

    highest_sold = None
    if top_sold:
        highest_sold = {
            "lot_id": top_sold.id,
            "lot_no": top_sold.lot_no,
            "player": top_sold.player.name,
            "team": top_sold.sold_to_team.name if top_sold.sold_to_team else "",
            "price": str(top_sold.sold_price or 0),
        }

    sold_total = AuctionLot.objects.filter(auction=lot.auction, status=AuctionLot.Status.SOLD).count()
    unsold_total = AuctionLot.objects.filter(
        auction=lot.auction,
        status__in=[
            AuctionLot.Status.PENDING,
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.UNSOLD,
        ],
    ).count()

    return JsonResponse(
        {
            "ok": True,
            "lot_id": lot.id,
            "status": "SOLD",
            "sold_to": {"id": team.id, "name": team.name},
            "sold_price": str(sold_price),
            "auction_completed": done,
            "auction_fully_completed": fully_done,
            "highest_sold": highest_sold,
            "counts": {"sold": sold_total, "unsold": unsold_total},
        }
    )


@login_required
@require_POST
@csrf_exempt
def mark_unsold(request, lot_id: int):
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    if lot.status in [AuctionLot.Status.SOLD, AuctionLot.Status.UNSOLD, AuctionLot.Status.WITHDRAWN]:
        return JsonResponse({"ok": False, "error": "Lot already closed."}, status=400)

    # Safety: if player is already assigned/retained, withdraw and block marking UNSOLD.
    assigned_exists = (
        TeamPlayer.objects.filter(tournament=lot.auction.tournament, player_id=lot.player_id)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .exists()
    )
    if assigned_exists:
        AuctionLot.objects.filter(pk=lot.pk).exclude(
            status__in=[AuctionLot.Status.SOLD, AuctionLot.Status.WITHDRAWN]
        ).update(status=AuctionLot.Status.WITHDRAWN)
        return JsonResponse({"ok": False, "error": "Player is already assigned to a team."}, status=400)

    lot.status = AuctionLot.Status.UNSOLD
    lot.save(update_fields=["status", "updated_at"])

    _log_event(
        auction=lot.auction,
        event_type=AuctionEvent.EventType.UNSOLD,
        level=AuctionEvent.Level.UNSOLD,
        message=f"{lot.player.name} went UNSOLD.",
        lot=lot,
        player=lot.player,
    )

    _relist_restricted_unsold_if_needed(lot.auction)
    done = _auto_complete_auction_if_done(lot.auction)
    fully_done = not AuctionLot.objects.filter(
        auction=lot.auction,
        status__in=[
            AuctionLot.Status.PENDING,
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.UNSOLD,
        ],
    ).exists()

    return JsonResponse(
        {
            "ok": True,
            "lot_id": lot.id,
            "status": "UNSOLD",
            "auction_completed": done,
            "auction_fully_completed": fully_done,
        }
    )


@login_required
@require_POST
@csrf_exempt
def next_lot(request, lot_id: int):
    """
    Returns the next PENDING lot.
    - Prefer lot_no > current lot_no
    - If none, wrap to the first PENDING lot
    """
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    # Keep auction consistent with team assignments
    _withdraw_open_lots_for_assigned_players(lot.auction)
    _relist_restricted_unsold_if_needed(lot.auction)
    restricted_lot_no, _rules = _active_restricted_lot(lot.auction)

    qs = (
        AuctionLot.objects.filter(auction=lot.auction, status=AuctionLot.Status.PENDING)
        .select_related("player")
    )
    if restricted_lot_no:
        qs = qs.filter(lot_no=restricted_lot_no)

    next_l = (
        qs.filter(
            Q(lot_no__gt=lot.lot_no)
            | Q(lot_no=lot.lot_no, lot_order__gt=lot.lot_order)
            | Q(lot_no=lot.lot_no, lot_order=lot.lot_order, id__gt=lot.id)
        )
        .order_by("lot_no", "lot_order", "id")
        .first()
    )
    if not next_l:
        next_l = qs.order_by("lot_no", "lot_order", "id").first()

    if not next_l:
        if restricted_lot_no:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"Lot {restricted_lot_no} must be fully SOLD before moving ahead.",
                },
                status=400,
            )
        return JsonResponse({"ok": False, "error": "No pending lots remaining."}, status=404)

    _ensure_lot_start_event(next_l)
    appearance_no = _appearance_no(next_l)

    return JsonResponse(
        {
            "ok": True,
            "next": {
                "lot_id": next_l.id,
                "lot_no": next_l.lot_no,
                "appearance_no": appearance_no,
                "player": {
                    "id": next_l.player_id,
                    "name": next_l.player.name,
                    "role": next_l.player.role or "",
                    "base": str(next_l.base_price_snapshot or 0),
                    "reserve": str(next_l.player.reserve_price or 0),
                    "city": next_l.player.city or "",
                    "age": next_l.player.age or "",
                    "photo": next_l.player.photo.url if next_l.player.photo else "",
                    "stats": next_l.player.stats or {},
                },
            },
        }
    )


@login_required
@require_GET
def lot_bids(request, lot_id: int):
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    bids = (
        Bid.objects.filter(lot=lot, is_valid=True)
        .select_related("team")
        .order_by("-amount", "-bid_time")[:20]
    )

    data = []
    for b in bids:
        data.append(
            {
                "id": b.id,
                "team": {"id": b.team_id, "name": b.team.name},
                "amount": str(b.amount),
                "time": b.bid_time.strftime("%H:%M:%S"),
            }
        )

    return JsonResponse({"ok": True, "lot_id": lot.id, "bids": data})


@login_required
@require_GET
def auction_sold_list(request, auction_id: int):
    auction = get_object_or_404(Auction.objects.select_related("tournament"), pk=auction_id)

    if not can_manage_tournament(request.user, auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    q = (request.GET.get("q") or "").strip()

    ordered_ids = list(
        AuctionLot.objects.filter(auction=auction)
        .order_by("lot_no", "lot_order", "id")
        .values_list("id", flat=True)
    )
    entry_map = {lot_id: idx for idx, lot_id in enumerate(ordered_ids, start=1)}

    qs = (
        AuctionLot.objects.filter(auction=auction, status=AuctionLot.Status.SOLD)
        .select_related("player", "sold_to_team")
        .order_by("lot_no", "lot_order", "id")
    )

    if q:
        qs = qs.filter(Q(player__name__icontains=q) | Q(sold_to_team__name__icontains=q))

    rows = []
    for lot in qs:
        rows.append(
            {
                "lot_id": lot.id,
                "lot_no": lot.lot_no,
                "entry_no": entry_map.get(lot.id) or "",
                "name": lot.player.name,
                "team": lot.sold_to_team.name if lot.sold_to_team_id else "",
                "price": str(lot.sold_price or 0),
            }
        )

    return JsonResponse({"ok": True, "rows": rows})


@login_required
@require_GET
def auction_unsold_list(request, auction_id: int):
    """UNSOLD + upcoming (PENDING/RUNNING) players."""

    auction = get_object_or_404(Auction.objects.select_related("tournament"), pk=auction_id)

    if not can_manage_tournament(request.user, auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    q = (request.GET.get("q") or "").strip()

    assigned_player_ids = _assigned_player_ids_qs(auction.tournament)

    qs = (
        AuctionLot.objects.filter(
            auction=auction,
            status__in=[
                AuctionLot.Status.PENDING,
                AuctionLot.Status.RUNNING,
                AuctionLot.Status.UNSOLD,
            ],
        )
        .exclude(player_id__in=assigned_player_ids)
        .select_related("player")
        .order_by("lot_no", "lot_order", "id")
    )

    if q:
        qs = qs.filter(player__name__icontains=q)

    rows = [{"name": lot.player.name} for lot in qs]
    return JsonResponse({"ok": True, "rows": rows})


@login_required
@require_POST
@csrf_exempt
def undo_last_bid(request, lot_id: int):
    lot = get_object_or_404(AuctionLot.objects.select_related("auction__tournament"), pk=lot_id)

    if not can_manage_tournament(request.user, lot.auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    with transaction.atomic():
        last_bid = (
            Bid.objects.filter(lot=lot, is_valid=True)
            .order_by("-bid_time", "-id")
            .select_related("team")
            .first()
        )
        if not last_bid:
            return JsonResponse({"ok": False, "error": "No bids to undo."}, status=400)

        # Invalidate last bid (audit-friendly)
        last_bid.is_valid = False
        last_bid.save(update_fields=["is_valid"])

        # Recompute highest after undo
        new_top = (
            Bid.objects.filter(lot=lot, is_valid=True)
            .order_by("-amount", "-bid_time")
            .select_related("team")
            .first()
        )

        if new_top:
            new_amount = new_top.amount
            new_team = new_top.team
            # Keep lot RUNNING if there are still bids
            if lot.status == AuctionLot.Status.PENDING:
                lot.status = AuctionLot.Status.RUNNING
                lot.save(update_fields=["status", "updated_at"])
        else:
            new_amount = lot.base_price_snapshot or 0
            new_team = None
            # If no bids left, revert to PENDING (nice UX)
            if lot.status == AuctionLot.Status.RUNNING:
                lot.status = AuctionLot.Status.PENDING
                lot.save(update_fields=["status", "updated_at"])

    return JsonResponse(
        {
            "ok": True,
            "undone_bid_id": last_bid.id,
            "new": {
                "amount": str(new_amount),
                "team": {"id": new_team.id, "name": new_team.name} if new_team else None,
            },
        }
    )


@login_required
@require_POST
@csrf_exempt
def end_auction(request, auction_id: int):
    auction = get_object_or_404(Auction.objects.select_related("tournament"), pk=auction_id)

    if not can_manage_tournament(request.user, auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    # Mark auction closed (idempotent)
    if auction.status != Auction.Status.CLOSED:
        auction.status = Auction.Status.CLOSED
        auction.save(update_fields=["status", "updated_at"])

    open_exists = AuctionLot.objects.filter(
        auction=auction,
        status__in=[AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING],
    ).exists()

    fully_done = not AuctionLot.objects.filter(
        auction=auction,
        status__in=[
            AuctionLot.Status.PENDING,
            AuctionLot.Status.RUNNING,
            AuctionLot.Status.UNSOLD,
        ],
    ).exists()

    return JsonResponse(
        {
            "ok": True,
            "status": auction.status,
            "auction_completed": not open_exists,
            "auction_fully_completed": fully_done,
        }
    )


@login_required
@require_POST
@csrf_exempt
def reopen_unsold(request, auction_id: int):
    auction = get_object_or_404(Auction.objects.select_related("tournament"), pk=auction_id)

    if not can_manage_tournament(request.user, auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    # Keep auction consistent with team assignments
    _withdraw_open_lots_for_assigned_players(auction)

    # Reopen UNSOLD lots back to PENDING
    assigned_player_ids = _assigned_player_ids_qs(auction.tournament)

    qs = AuctionLot.objects.filter(auction=auction, status=AuctionLot.Status.UNSOLD).exclude(
        player_id__in=assigned_player_ids
    )
    unsold_ids = list(qs.values_list("id", flat=True))

    reopened = qs.update(status=AuctionLot.Status.PENDING)

    # Reset bid state for relisted lots
    if unsold_ids:
        Bid.objects.filter(lot_id__in=unsold_ids, is_valid=True).update(is_valid=False)

    # If you want, you can also revert auction status back to LIVE/RUNNING.
    # We'll keep it simple: if reopened > 0, set auction to LIVE.
    if reopened > 0:
        # Use your enum name here if different (DRAFT/LIVE/COMPLETED)
        if auction.status != Auction.Status.LIVE:
            auction.status = Auction.Status.LIVE
            auction.save(update_fields=["status", "updated_at"])

    return JsonResponse({"ok": True, "reopened": reopened})


@require_GET
def auction_updates_public(request, slug: str):
    tournament = get_object_or_404(Tournament.objects.select_related("auction"), slug=slug)
    auction = getattr(tournament, "auction", None)
    return render(
        request,
        "auctions/auction_updates.html",
        {"tournament": tournament, "auction": auction, "hide_nav": True},
    )


@require_GET
def auction_stage_public(request, slug: str):
    tournament = get_object_or_404(Tournament.objects.select_related("auction"), slug=slug)
    auction = getattr(tournament, "auction", None)
    snapshot = _public_auction_snapshot(auction) if auction else None
    return render(
        request,
        "auctions/auction_stage.html",
        {
            "tournament": tournament,
            "auction": auction,
            "snapshot": snapshot,
            "hide_nav": True,
        },
    )


@require_GET
def auction_updates_feed(request, slug: str):
    tournament = get_object_or_404(Tournament.objects.select_related("auction"), slug=slug)
    auction = getattr(tournament, "auction", None)
    if not auction:
        return JsonResponse({"ok": False, "error": "No auction found."}, status=404)

    try:
        after_id = int(request.GET.get("after", 0) or 0)
    except (TypeError, ValueError):
        after_id = 0
    try:
        before_id = int(request.GET.get("before", 0) or 0)
    except (TypeError, ValueError):
        before_id = 0

    base_qs = AuctionEvent.objects.filter(auction=auction).select_related("lot", "player", "team")

    if after_id > 0:
        events = list(base_qs.filter(id__gt=after_id).order_by("id")[:100])
    elif before_id > 0:
        events = list(base_qs.filter(id__lt=before_id).order_by("-id")[:100])
    else:
        # When first loading the page, return the latest 100 events in ascending order.
        # The client prepends them so the newest event stays at the top.
        events = list(base_qs.order_by("-id")[:100])
        events.reverse()

    data = [_serialize_auction_event(e) for e in events]

    has_older = False
    if data:
        oldest_loaded_id = min(item["id"] for item in data)
        has_older = base_qs.filter(id__lt=oldest_loaded_id).exists()

    return JsonResponse(
        {
            "ok": True,
            "events": data,
            "has_older": has_older,
            "snapshot": _public_auction_snapshot(auction),
        }
    )
