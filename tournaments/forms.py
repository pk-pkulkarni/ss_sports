from django.contrib.auth import get_user_model
from datetime import date

from django import forms

from auctions.models import Team, Player, TeamAccess

from .models import Tournament

DEFAULT_PLAYER_BASE_PRICE = 10000


class TournamentCreateForm(forms.ModelForm):
    class Meta:
        model = Tournament
        fields = ["name", "season_year", "city", "venue", "status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, (forms.TextInput, forms.NumberInput, forms.Select, forms.DateInput)):
                f.widget.attrs.setdefault("class", "form-control")


class TeamCreateForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = [
            "name",
            "short_name",
            "logo",
            "tagline",
            "primary_color",
            "secondary_color",
            "accent_color",
            "purse_total",
            "purse_remaining",
            "min_players",
            "max_players",
            "is_active",
        ]
        widgets = {
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Defaults
        self.fields["is_active"].initial = True
        self.fields["purse_total"].required = False
        self.fields["purse_remaining"].required = False

        for name, f in self.fields.items():
            if name == "is_active":
                continue
            if isinstance(f.widget, (forms.TextInput, forms.NumberInput, forms.Select, forms.DateInput)):
                f.widget.attrs.setdefault("class", "form-control")
            if isinstance(f.widget, forms.ClearableFileInput):
                f.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()

        purse_total = cleaned.get("purse_total")
        purse_remaining = cleaned.get("purse_remaining")

        if purse_total is None:
            purse_total = 0
            cleaned["purse_total"] = purse_total

        # If remaining is blank, default to total.
        if purse_remaining is None:
            cleaned["purse_remaining"] = purse_total

        return cleaned


class TeamAccessForm(forms.ModelForm):
    class Meta:
        model = TeamAccess
        fields = ["user", "linked_player", "role", "is_primary", "is_active"]
        widgets = {
            "is_primary": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        tournament = kwargs.pop("tournament", None)
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = get_user_model().objects.order_by("username")
        if tournament:
            self.fields["linked_player"].queryset = Player.objects.filter(tournament=tournament).order_by("name")
        else:
            self.fields["linked_player"].queryset = Player.objects.none()
        self.fields["linked_player"].required = False
        self.fields["is_active"].initial = True

        for name, f in self.fields.items():
            if isinstance(f.widget, forms.Select):
                f.widget.attrs.setdefault("class", "form-select")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_primary") and not cleaned.get("is_active"):
            self.add_error("is_active", "Primary access must stay active.")
        return cleaned


class PlayerForm(forms.ModelForm):
    matches = forms.CharField(required=False)
    runs = forms.CharField(required=False)
    wickets = forms.CharField(required=False)
    overs = forms.CharField(required=False)
    eco = forms.CharField(required=False)
    bat_avg = forms.CharField(required=False, label="Bat Avg")
    bat_sr = forms.CharField(required=False, label="Bat SR")

    class Meta:
        model = Player
        fields = [
            "name",
            "photo",
            "is_active",
            "phone",
            "city",
            "age",
            "role",
            "batting_style",
            "bowling_style",
            "base_price",
            "reserve_price",
            "notes",
        ]
        widgets = {
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["is_active"].initial = True
        if not (self.instance and self.instance.pk):
            self.fields["base_price"].initial = DEFAULT_PLAYER_BASE_PRICE
        # Populate stat fields from instance.stats
        stats = {}
        if self.instance and self.instance.pk and isinstance(self.instance.stats, dict):
            stats = self.instance.stats
        for key in ["matches", "runs", "wickets", "overs", "eco", "bat_avg", "bat_sr"]:
            if key in self.fields:
                self.fields[key].initial = stats.get(key, "")

        for name, f in self.fields.items():
            if name == "is_active":
                continue
            if isinstance(
                    f.widget,
                    (
                            forms.TextInput,
                            forms.NumberInput,
                            forms.Select,
                            forms.DateInput,
                            forms.Textarea,
                    ),
            ):
                f.widget.attrs.setdefault("class", "form-control")
            if isinstance(f.widget, forms.ClearableFileInput):
                f.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        stats = {}
        for key in ["matches", "runs", "wickets", "overs", "eco", "bat_avg", "bat_sr"]:
            val = cleaned.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                val = val.strip()
            if val != "":
                stats[key] = val
        cleaned["stats"] = stats or None
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.stats = self.cleaned_data.get("stats")
        if instance.base_price in (None, ""):
            instance.base_price = DEFAULT_PLAYER_BASE_PRICE
        if commit:
            instance.save()
        return instance


class PublicPlayerRegistrationForm(forms.ModelForm):
    """Public (no-login) registration form for adding a Player to a Tournament."""

    BUILDING_CHOICES = [(f"D-{i}", f"D-{i}") for i in range(1, 11)]

    gender = forms.ChoiceField(
        choices=[("", "Select")] + list(Player.Gender.choices),
        required=True,
    )

    building = forms.ChoiceField(
        choices=[("", "Select")] + BUILDING_CHOICES,
        required=True,
    )

    payment_screenshot = forms.ImageField(
        required=True,
        label="Payment screenshot",
    )

    jersey_no = forms.IntegerField(
        required=True,
        min_value=0,
        label="Jersey no.",
    )

    jersey_name = forms.CharField(
        required=True,
        max_length=30,
        label="Jersey name",
    )

    jersey_size = forms.ChoiceField(
        choices=[("", "Select")] + list(Player.JerseySize.choices),
        required=True,
        label="Jersey size",
    )

    class Meta:
        model = Player
        fields = [
            "tournament",
            "name",
            "gender",
            "photo",
            "building",
            "flat_no",
            "dob",
            "phone",
            "payment_screenshot",
            "jersey_no",
            "jersey_name",
            "jersey_size",
        ]
        widgets = {
            "dob": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Only show tournaments that are not archived.
        qs = Tournament.objects.exclude(
            status=Tournament.Status.ARCHIVED
        ).order_by("-created_at")
        self.fields["tournament"].queryset = qs

        if qs.exists():
            first_tournament = qs.first()
            self.fields["tournament"].initial = first_tournament.pk
            self.fields["tournament"].disabled = True  # disables UI AND keeps value on POST
            self.fields["tournament"].empty_label = None  # optional: removes "Select tournament"
        else:
            self.fields["tournament"].disabled = True
            self.fields["tournament"].empty_label = "No tournaments available"

        # Public form requirements.
        self.fields["tournament"].required = True
        self.fields["name"].required = True
        self.fields["gender"].required = True
        self.fields["building"].required = True
        self.fields["flat_no"].required = True
        self.fields["dob"].required = True
        self.fields["phone"].required = True
        self.fields["payment_screenshot"].required = True
        self.fields["jersey_no"].required = True
        self.fields["jersey_name"].required = True
        self.fields["jersey_size"].required = True

        # Helpful placeholders / hints.
        self.fields["name"].widget.attrs.setdefault("placeholder", "Full name")
        self.fields["flat_no"].widget.attrs.setdefault("placeholder", "e.g. 1204")
        self.fields["phone"].widget.attrs.setdefault("placeholder", "e.g. 98XXXXXXXX")
        self.fields["photo"].widget.attrs.setdefault("accept", "image/*")
        self.fields["payment_screenshot"].widget.attrs.setdefault("accept", "image/*")
        self.fields["jersey_no"].widget.attrs.setdefault("placeholder", "e.g. 7")
        self.fields["jersey_name"].widget.attrs.setdefault("placeholder", "Name on jersey")

        for name, f in self.fields.items():
            if isinstance(f.widget, forms.ClearableFileInput):
                f.widget.attrs.setdefault("class", "form-control")
            elif isinstance(f.widget, (forms.Select,)):
                f.widget.attrs.setdefault("class", "form-select")
            elif isinstance(f.widget, (forms.TextInput, forms.DateInput, forms.NumberInput)):
                f.widget.attrs.setdefault("class", "form-control")

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Derive age from DOB for convenience.
        dob = self.cleaned_data.get("dob")
        if dob:
            today = date.today()
            instance.age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        # Public registrations are active by default.
        instance.is_active = True
        if instance.base_price in (None, "") or instance.base_price == 0:
            instance.base_price = DEFAULT_PLAYER_BASE_PRICE

        if commit:
            instance.save()
        return instance
