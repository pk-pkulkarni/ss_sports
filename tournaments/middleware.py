from django.http import HttpResponseForbidden

from .permissions import is_tournament_viewer


class TournamentViewerReadOnlyMiddleware:
    """Reject writes by viewer accounts, including direct API requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}
            and is_tournament_viewer(request.user)
            and request.path_info not in {"/logout/", "/password/change/"}
        ):
            return HttpResponseForbidden("This account has read-only access.")
        return self.get_response(request)
