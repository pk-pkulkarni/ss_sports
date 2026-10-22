from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0012_alter_player_jersey_size"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="is_eligible_for_auction",
            field=models.BooleanField(
                default=True,
                help_text="Only eligible players are included in auction lots and owner dashboards.",
            ),
        ),
    ]
