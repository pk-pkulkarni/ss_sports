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


def _auto_complete_auction_if_done(auction):
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
        for x in TeamPlayer.objects.filter(tournament=tournament)
        .values("team_id")
        .annotate(count=Count("id"))
    }

    teams = list(teams_qs)
    for t in teams:
        t.players_count = count_map.get(t.id, 0)

    current_lot = None
    highest_sold = None
    if auction:
        current_lot = (
            AuctionLot.objects.filter(auction=auction, status=AuctionLot.Status.PENDING)
            .select_related("player", "sold_to_team")
            .order_by("lot_no", "lot_order", "id")
            .first()
        )
        if not current_lot:
            current_lot = (
                AuctionLot.objects.filter(auction=auction)
                .select_related("player", "sold_to_team")
                .order_by("lot_no", "lot_order", "id")
                .first()
            )
        if current_lot and current_lot.status in [AuctionLot.Status.PENDING, AuctionLot.Status.RUNNING]:
            _ensure_lot_start_event(current_lot)

        top_sold = _get_highest_sold_lot(auction)
        if top_sold:
            highest_sold = {
                "player": top_sold.player.name,
                "team": top_sold.sold_to_team.name if top_sold.sold_to_team else "",
                "price": top_sold.sold_price,
                "lot_no": top_sold.lot_no,
            }

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

    try:
        payload = json.loads(request.body.decode("utf-8"))
        team_id = int(payload.get("team_id"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    team = get_object_or_404(Team, pk=team_id, tournament=lot.auction.tournament, is_active=True)

    with transaction.atomic():
        current = _get_current_highest(lot)
        next_amount = _allowed_next_amount(current)

        # OPTIONAL (strict): ensure team has enough purse_remaining if set
        # If purse_total is 0, we treat it as not configured yet and allow bidding.
        if (team.purse_total or 0) > 0:
            if (team.purse_remaining or 0) < next_amount:
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

    with transaction.atomic():
        team = _get_highest_team(lot)
        if not team:
            return JsonResponse({"ok": False, "error": "No bids found. Can't mark SOLD."}, status=400)

        sold_price = _get_current_highest(lot)

        # purse check (if configured)
        if (team.purse_total or 0) > 0 and (team.purse_remaining or 0) < sold_price:
            return JsonResponse({"ok": False, "error": f"{team.name} doesn't have purse for ₹{sold_price}."}, status=400)

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

        done = _auto_complete_auction_if_done(lot.auction)
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

    return JsonResponse(
        {
            "ok": True,
            "lot_id": lot.id,
            "status": "SOLD",
            "sold_to": {"id": team.id, "name": team.name},
            "sold_price": str(sold_price),
            "auction_completed": done,
            "highest_sold": highest_sold,
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

    done = _auto_complete_auction_if_done(lot.auction)

    return JsonResponse({"ok": True, "lot_id": lot.id, "status": "UNSOLD", "auction_completed": done})


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

    qs = (
        AuctionLot.objects.filter(auction=lot.auction, status=AuctionLot.Status.PENDING)
        .select_related("player")
    )

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
def reopen_unsold(request, auction_id: int):
    auction = get_object_or_404(Auction.objects.select_related("tournament"), pk=auction_id)

    if not can_manage_tournament(request.user, auction.tournament):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    # Reopen UNSOLD lots back to PENDING
    qs = AuctionLot.objects.filter(auction=auction, status=AuctionLot.Status.UNSOLD)

    reopened = qs.update(status=AuctionLot.Status.PENDING)

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
def auction_updates_feed(request, slug: str):
    tournament = get_object_or_404(Tournament.objects.select_related("auction"), slug=slug)
    auction = getattr(tournament, "auction", None)
    if not auction:
        return JsonResponse({"ok": False, "error": "No auction found."}, status=404)

    try:
        after_id = int(request.GET.get("after", 0) or 0)
    except (TypeError, ValueError):
        after_id = 0

    base_qs = AuctionEvent.objects.filter(auction=auction).select_related("lot", "player", "team")

    # When first loading the page (after_id=0), return the *latest* 100 events,
    # but in ascending id order so the client can prepend them to show newest first.
    if after_id > 0:
        events = list(base_qs.filter(id__gt=after_id).order_by("id")[:100])
    else:
        events = list(base_qs.order_by("-id")[:100])
        events.reverse()

    data = []
    for e in events:
        data.append(
            {
                "id": e.id,
                "message": e.message,
                "level": e.level,
                "event_type": e.event_type,
                "time": e.created_at.strftime("%H:%M:%S"),
                "player": e.player.name if e.player_id else "",
                "team": e.team.name if e.team_id else "",
                "lot_no": e.lot.lot_no if e.lot_id else None,
            }
        )

    return JsonResponse({"ok": True, "events": data})
