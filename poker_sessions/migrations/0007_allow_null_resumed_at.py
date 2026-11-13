from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("poker_sessions", "0006_make_locations_shared"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pokersession",
            name="resumed_at",
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, null=True),
        ),
    ]
