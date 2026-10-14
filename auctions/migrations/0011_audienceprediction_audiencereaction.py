from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("auctions", "0010_auction_presentation_state_and_events")]

    operations = [
        migrations.CreateModel(
            name="AudiencePrediction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("auction", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audience_predictions", to="auctions.auction")),
                ("lot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audience_predictions", to="auctions.auctionlot")),
                ("team", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audience_predictions", to="auctions.team")),
            ],
            options={"db_table": "audience_prediction"},
        ),
        migrations.CreateModel(
            name="AudienceReaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reaction", models.CharField(choices=[("CLAP", "Clap"), ("FIRE", "Fire"), ("WOW", "Wow")], max_length=8)),
                ("session_key", models.CharField(max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("auction", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audience_reactions", to="auctions.auction")),
                ("lot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audience_reactions", to="auctions.auctionlot")),
            ],
            options={"db_table": "audience_reaction"},
        ),
        migrations.AddConstraint(model_name="audienceprediction", constraint=models.UniqueConstraint(fields=("lot", "session_key"), name="one_prediction_per_lot_session")),
        migrations.AddIndex(model_name="audienceprediction", index=models.Index(fields=["auction", "lot"], name="audience_pr_auction_955e6c_idx")),
        migrations.AddIndex(model_name="audiencereaction", index=models.Index(fields=["auction", "lot"], name="audience_re_auction_efb7bc_idx")),
        migrations.AddIndex(model_name="audiencereaction", index=models.Index(fields=["session_key", "created_at"], name="audience_re_session_0e9ade_idx")),
    ]
