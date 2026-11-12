from django.db import migrations


def create_viewers_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).get_or_create(name="Tournament Viewers")


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0002_alter_tournament_slug"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(create_viewers_group, migrations.RunPython.noop)]
