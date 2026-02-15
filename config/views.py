from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from tournaments.forms import PublicPlayerRegistrationForm


def landing(request):
    """Public landing page with player registration (no login required)."""

    if request.method == "POST":
        registration_form = PublicPlayerRegistrationForm(request.POST, request.FILES)
        if registration_form.is_valid():
            player = registration_form.save()
            messages.success(
                request,
                f"Registration successful: {player.name} registered for {player.tournament.name}.",
            )
            return redirect(f"{reverse('landing')}#registration")

        messages.error(request, "Please correct the errors below and try again.")
    else:
        registration_form = PublicPlayerRegistrationForm()

    has_tournaments = registration_form.fields["tournament"].queryset.exists()

    return render(
        request,
        "landing.html",
        {
            "registration_form": registration_form,
            "has_tournaments": has_tournaments,
            "qr_code_url": f"{settings.MEDIA_URL}qr/player_registration_qr.png",
        },
    )
