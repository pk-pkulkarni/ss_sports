def can_manage_tournament(user, tournament) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or tournament.organizer_id == user.id
