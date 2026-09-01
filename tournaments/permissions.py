from auctions.models import TeamAccess


def can_manage_tournament(user, tournament) -> bool:
    if not user or not user.is_authenticated:
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

    if can_manage_tournament(user, team.tournament):
        return True

    return user_team_access_qs(user).filter(team=team).exists()
