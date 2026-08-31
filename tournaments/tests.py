from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from auctions.models import Player

from .forms import DEFAULT_PLAYER_BASE_PRICE, PlayerForm, PublicPlayerRegistrationForm
from .models import Tournament


class PlayerBasePriceFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="pass123")
        self.tournament = Tournament.objects.create(
            organizer=self.user,
            name="Base Price Cup",
            season_year=2026,
        )

    def test_new_player_form_defaults_base_price_to_10k(self):
        form = PlayerForm()
        self.assertEqual(form.fields["base_price"].initial, DEFAULT_PLAYER_BASE_PRICE)

    def test_edit_player_form_uses_database_base_price(self):
        player = Player.objects.create(
            tournament=self.tournament,
            name="Existing Player",
            base_price=25000,
            is_active=True,
        )

        form = PlayerForm(instance=player)
        self.assertEqual(form["base_price"].value(), 25000)

    def test_public_registration_defaults_base_price_to_10k(self):
        form = PublicPlayerRegistrationForm(
            data={
                "tournament": self.tournament.pk,
                "name": "Registered Player",
                "gender": Player.Gender.MALE,
                "building": "D-1",
                "flat_no": "101",
                "dob": "2000-01-01",
                "phone": "9999999999",
                "jersey_no": 7,
                "jersey_name": "REG",
                "jersey_size": Player.JerseySize.M,
            },
            files={
                "payment_screenshot": SimpleUploadedFile(
                    "payment.gif",
                    (
                        b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                        b"\x00\x00\x00\xff\xff\xff!\xf9\x04"
                        b"\x01\x00\x00\x00\x00,\x00\x00\x00"
                        b"\x00\x01\x00\x01\x00\x00\x02\x02D"
                        b"\x01\x00;"
                    ),
                    content_type="image/gif",
                )
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        player = form.save()
        self.assertEqual(player.base_price, DEFAULT_PLAYER_BASE_PRICE)
