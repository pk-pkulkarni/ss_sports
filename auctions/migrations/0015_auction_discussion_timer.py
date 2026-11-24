from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0014_alter_player_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="auction",
            name="discussion_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="discussion_lot",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="discussion_auctions", to="auctions.auctionlot"),
        ),
        migrations.AddField(
            model_name="auction",
            name="discussion_team",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="discussion_auctions", to="auctions.team"),
        ),
    ]
