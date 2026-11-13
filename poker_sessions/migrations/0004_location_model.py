from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_location_names(apps, schema_editor):
    PokerSession = apps.get_model("poker_sessions", "PokerSession")
    Location = apps.get_model("poker_sessions", "Location")
    locations = {}
    for session in PokerSession.objects.exclude(legacy_location="").iterator():
        key = (session.user_id, session.legacy_location)
        if key not in locations:
            locations[key], _ = Location.objects.get_or_create(
                user_id=session.user_id,
                name=session.legacy_location,
            )
        session.location_id = locations[key].pk
        session.save(update_fields=["location"])


def restore_location_names(apps, schema_editor):
    PokerSession = apps.get_model("poker_sessions", "PokerSession")
    for session in PokerSession.objects.select_related("location").iterator():
        session.legacy_location = session.location.name if session.location_id else ""
        session.save(update_fields=["legacy_location"])


class Migration(migrations.Migration):
    dependencies = [
        ("poker_sessions", "0003_alter_pokersession_game_alter_pokersession_stakes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("address", models.CharField(blank=True, max_length=255)),
                ("rating", models.PositiveSmallIntegerField(
                    choices=[
                        (1, "1 star"),
                        (2, "2 stars"),
                        (3, "3 stars"),
                        (4, "4 stars"),
                        (5, "5 stars"),
                    ],
                    default=3,
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="poker_locations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["name", "pk"]},
        ),
        migrations.RenameField(
            model_name="pokersession",
            old_name="location",
            new_name="legacy_location",
        ),
        migrations.AddField(
            model_name="pokersession",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sessions",
                to="poker_sessions.location",
            ),
        ),
        migrations.RunPython(move_location_names, restore_location_names),
        migrations.RemoveField(
            model_name="pokersession",
            name="legacy_location",
        ),
    ]
