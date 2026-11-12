from .permissions import is_tournament_viewer


def tournament_access(request):
    return {"readonly_portal": is_tournament_viewer(request.user)}
