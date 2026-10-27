from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import OwnPasswordChangeForm


@login_required
@require_http_methods(["GET", "POST"])
def change_password(request):
    if request.user.is_superuser:
        return HttpResponseForbidden("Use the admin site to change a superuser password.")

    form = OwnPasswordChangeForm(request.user, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new_password1"])
        request.user.save(update_fields=["password"])
        logout(request)
        messages.success(request, "Password changed. Sign in with your new password.")
        return redirect("login")

    return render(request, "registration/change_password.html", {"form": form})
