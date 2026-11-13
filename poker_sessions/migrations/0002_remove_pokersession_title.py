from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("poker_sessions", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pokersession",
            name="title",
        ),
    ]
