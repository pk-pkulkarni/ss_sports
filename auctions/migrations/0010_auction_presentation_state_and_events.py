from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0009_teamwatchlist"),
    ]

    operations = [
        migrations.AddField(
            model_name="auction",
            name="presentation_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="presentation_message",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="auction",
            name="presentation_state",
            field=models.CharField(
                choices=[
                    ("LIVE", "Live bidding"),
                    ("REVEAL", "Player reveal"),
                    ("FINAL_CALL", "Final call"),
                    ("BREAK", "Break"),
                    ("ANNOUNCEMENT", "Announcement"),
                ],
                default="LIVE",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="auction",
            name="presentation_version",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="auctionevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("LOT_START", "Lot Start"),
                    ("BID", "Bid"),
                    ("MILESTONE_1L", "Milestone 1L"),
                    ("SOLD", "Sold"),
                    ("UNSOLD", "Unsold"),
                    ("PLAYER_REVEAL", "Player Reveal"),
                    ("FINAL_CALL", "Final Call"),
                    ("BREAK", "Break"),
                    ("ANNOUNCEMENT", "Announcement"),
                    ("SHOW_RESUME", "Show Resume"),
                ],
                max_length=20,
            ),
        ),
    ]
