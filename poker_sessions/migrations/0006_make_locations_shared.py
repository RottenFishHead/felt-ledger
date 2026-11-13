from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("poker_sessions", "0005_alter_pokersession_stakes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="location",
            name="user",
        ),
    ]
