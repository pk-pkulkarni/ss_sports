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

from auctions.models import Auction, Team, TeamPlayer, Player, AuctionLot
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
    context = {
        "tournament": tournament,
        "teams": teams,
        "players": players,
        "total_players": players.count(),
        "active_players": players.filter(is_active=True).count(),
        "total_teams": teams.count(),
        "can_manage": can_manage_tournament(request.user, tournament),
        "player_form": PlayerForm(),
        "team_form": TeamCreateForm(),
    }
    return render(request, "tournaments/tournament_detail.html", context)


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

    auction = _ensure_auction(tournament)
    created, skipped = generate_random_lots_from_active_players(auction, max_lots=4, reset=True)

    messages.success(
        request,
        f"✅ Random lots generated (reset). Created: {created}. Skipped (already existed): {skipped}.",
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
                    {"team": team},
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
            player_html = render_to_string(
                "tournaments/partials/player_card.html",
                {
                    "p": player,
                    "tournament": tournament,
                    "can_manage": can_manage_tournament(request.user, tournament),
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
        # Excel sheet title rules: max 31 chars and can't contain: : \ / ? * [ ]
        name = re.sub(r"[:\\/\?\*\[\]]", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
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
