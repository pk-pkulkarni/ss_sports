"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views as config_views
from tournaments import views as tournament_views
from auctions import views as auction_views


urlpatterns = [
    # Public landing page
    path("", config_views.landing, name="landing"),

    # Auth
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(next_page="landing"), name="logout"),

    # Admin
    path("admin/", admin.site.urls),

    # Tournaments
    path("tournaments/", tournament_views.tournament_list, name="tournament_list"),
    path("tournaments/create/", tournament_views.tournament_create, name="tournament_create"),
    path("t/<slug:slug>/", tournament_views.tournament_detail, name="tournament_detail"),
    path("t/<slug:slug>/teams/create/", tournament_views.team_create, name="team_create"),
    path("t/<slug:slug>/teams/<int:team_id>/edit/", tournament_views.team_edit, name="team_edit"),
    path("t/<slug:slug>/owner/teams/<int:team_id>/", tournament_views.owner_dashboard, name="owner_dashboard"),
    path("t/<slug:slug>/players/create/", tournament_views.player_create, name="player_create"),
    path("t/<slug:slug>/players/update/", tournament_views.player_update, name="player_update"),
    path("t/<slug:slug>/players/<int:player_id>/delete/", tournament_views.player_delete, name="player_delete"),
    path("t/<slug:slug>/players/<int:player_id>/edit/", tournament_views.player_edit, name="player_edit"),
    path("t/<slug:slug>/auction/setup/", tournament_views.setup_auction, name="setup_auction"),
    path("t/<slug:slug>/generate-lots/", tournament_views.generate_lots, name="generate_lots"),
    path("t/<slug:slug>/generate-random-lots/", tournament_views.generate_random_lots, name="generate_random_lots"),
    path("t/<slug:slug>/lots/manage/", tournament_views.lot_manager, name="lot_manager"),
    path("t/<slug:slug>/lots/move/", tournament_views.move_lot_player, name="move_lot_player"),
    path("t/<slug:slug>/teams-dashboard/", tournament_views.teams_dashboard, name="teams_dashboard"),
    path("t/<slug:slug>/teams-export/", tournament_views.export_teams_xlsx, name="teams_export_xlsx"),
    path("t/<slug:slug>/players-export/", tournament_views.export_players_xlsx, name="players_export_xlsx"),

    # Auction screen (UI)
    path("t/<slug:slug>/auction/", auction_views.auction_screen, name="auction_screen"),
    path("t/<slug:slug>/auction/updates/", auction_views.auction_updates_public, name="auction_updates_public"),
    path("t/<slug:slug>/auction/stage/", auction_views.auction_stage_public, name="auction_stage_public"),
    path("t/<slug:slug>/auction/updates/feed/", auction_views.auction_updates_feed, name="auction_updates_feed"),

    # API-like endpoints (POST)
    path("auction/<int:lot_id>/bid/", auction_views.place_bid, name="place_bid"),
    path("auction/<int:lot_id>/mark_sold/", auction_views.mark_sold, name="mark_sold"),
    path("auction/<int:lot_id>/mark_unsold/", auction_views.mark_unsold, name="mark_unsold"),
    path("auction/<int:lot_id>/next/", auction_views.next_lot, name="next_lot"),
    path("auction/<int:lot_id>/bids/", auction_views.lot_bids, name="lot_bids"),
    path("auction/<int:auction_id>/sold/", auction_views.auction_sold_list, name="auction_sold_list"),
    path("auction/<int:auction_id>/unsold/", auction_views.auction_unsold_list, name="auction_unsold_list"),
    path("auction/<int:lot_id>/undo_last_bid/", auction_views.undo_last_bid, name="undo_last_bid"),
    path("auction/<int:auction_id>/reopen_unsold/", auction_views.reopen_unsold, name="reopen_unsold"),
    path("auction/<int:auction_id>/end/", auction_views.end_auction, name="end_auction"),
]

# Media files (DEV only)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
