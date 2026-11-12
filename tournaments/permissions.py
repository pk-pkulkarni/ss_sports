from auctions.models import TeamAccess

TOURNAMENT_VIEWERS_GROUP = "Tournament Viewers"


def is_tournament_viewer(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and not user.is_superuser
        and user.groups.filter(name=TOURNAMENT_VIEWERS_GROUP).exists()
    )


def can_view_tournament(user, tournament) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or tournament.organizer_id == user.id:
        return True
    return is_tournament_viewer(user) and tournament.organizer.is_superuser


def can_manage_tournament(user, tournament) -> bool:
    if not user or not user.is_authenticated:
        return False
    if is_tournament_viewer(user):
        return False
    return user.is_superuser or tournament.organizer_id == user.id


def user_team_access_qs(user):
    if not user or not user.is_authenticated:
        return TeamAccess.objects.none()

    if user.is_superuser:
        return TeamAccess.objects.select_related("team", "team__tournament", "user")

    return TeamAccess.objects.filter(
        user=user,
        is_active=True,
        team__is_active=True,
    ).select_related("team", "team__tournament", "user")


def can_access_team_dashboard(user, team) -> bool:
    if not user or not user.is_authenticated:
        return False

    if can_view_tournament(user, team.tournament):
        return True

    return user_team_access_qs(user).filter(team=team).exists()
