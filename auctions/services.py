import random

from django.db import models, transaction

from .models import Auction, AuctionLot, Player, TeamPlayer


def generate_lots_from_active_players(auction: Auction) -> tuple[int, int]:
    """
    Create AuctionLot rows for all ACTIVE players in the auction's tournament.

    - Uses sequential lot_no after the current max lot_no.
    - Skips players that already have lots in this auction.
    - Skips players already assigned to a team (TeamPlayer) in this tournament.

    Returns: (created_count, skipped_count)
    """

    tournament = auction.tournament

    existing_player_ids = set(
        AuctionLot.objects.filter(auction=auction).values_list("player_id", flat=True)
    )

    assigned_player_ids = set(
        TeamPlayer.objects.filter(tournament=tournament)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .values_list("player_id", flat=True)
    )

    max_lot_no = (
        AuctionLot.objects.filter(auction=auction)
        .aggregate(models.Max("lot_no"))
        .get("lot_no__max")
        or 0
    )

    active_players = Player.objects.filter(tournament=tournament, is_active=True).order_by(
        "name"
    )

    lots_to_create = []
    next_no = max_lot_no + 1
    skipped = 0

    for p in active_players:
        if p.id in existing_player_ids or p.id in assigned_player_ids:
            skipped += 1
            continue

        lots_to_create.append(
            AuctionLot(
                auction=auction,
                player=p,
                lot_no=next_no,
                lot_order=1,
                status=AuctionLot.Status.PENDING,
                base_price_snapshot=p.base_price or 0,
            )
        )
        next_no += 1

    with transaction.atomic():
        AuctionLot.objects.bulk_create(lots_to_create)

    return len(lots_to_create), skipped


def resequence_lot_orders(auction: Auction, lot_no: int) -> None:
    """
    Ensure lot_order is 1..N for a given lot_no (stable by current order).
    """
    lots = list(
        AuctionLot.objects.filter(auction=auction, lot_no=lot_no)
        .order_by("lot_order", "id")
    )
    updates = []
    for idx, lot in enumerate(lots, start=1):
        if lot.lot_order != idx:
            lot.lot_order = idx
            updates.append(lot)
    if updates:
        AuctionLot.objects.bulk_update(updates, ["lot_order"])


def generate_random_lots_from_active_players(
    auction: Auction, max_lots: int = 4, reset: bool = True
) -> tuple[int, int]:
    """
    Randomly distribute ACTIVE players into up to max_lots lots.
    - If reset=True, existing lots (and bids) for this auction are deleted.
    - Players are shuffled and assigned in round-robin across lot_no 1..N.
    - Each lot gets a randomized lot_order.
    - Players already assigned to a team (TeamPlayer) in this tournament are excluded.

    Returns: (created_count, skipped_count)
    """
    tournament = auction.tournament

    if reset:
        AuctionLot.objects.filter(auction=auction).delete()

    existing_player_ids = set(
        AuctionLot.objects.filter(auction=auction).values_list("player_id", flat=True)
    )

    assigned_player_ids = set(
        TeamPlayer.objects.filter(tournament=tournament)
        .exclude(status=TeamPlayer.Status.RELEASED)
        .values_list("player_id", flat=True)
    )

    players = list(Player.objects.filter(tournament=tournament, is_active=True))
    if not players:
        return 0, 0

    random.shuffle(players)

    # Filter eligible players and count skipped (already have lots OR already assigned to a team)
    eligible = []
    skipped = 0
    for p in players:
        if p.id in existing_player_ids or p.id in assigned_player_ids:
            skipped += 1
            continue
        eligible.append(p)

    if not eligible:
        return 0, skipped

    lot_count = min(max_lots, max(1, len(eligible)))
    lot_numbers = list(range(1, lot_count + 1))

    # Round-robin distribution
    buckets = {n: [] for n in lot_numbers}
    for idx, p in enumerate(eligible):
        lot_no = lot_numbers[idx % lot_count]
        buckets[lot_no].append(p)

    lots_to_create = []
    for lot_no, bucket in buckets.items():
        if not bucket:
            continue
        random.shuffle(bucket)
        for order_idx, p in enumerate(bucket, start=1):
            lots_to_create.append(
                AuctionLot(
                    auction=auction,
                    player=p,
                    lot_no=lot_no,
                    lot_order=order_idx,
                    status=AuctionLot.Status.PENDING,
                    base_price_snapshot=p.base_price or 0,
                )
            )

    with transaction.atomic():
        AuctionLot.objects.bulk_create(lots_to_create)

    return len(lots_to_create), skipped
