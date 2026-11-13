from decimal import Decimal
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="PokerSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(default="Poker session", max_length=120)),
                ("location", models.CharField(blank=True, max_length=120)),
                ("game", models.CharField(default="No-Limit Hold'em", max_length=80)),
                ("stakes", models.CharField(blank=True, help_text="e.g. $1/$2", max_length=40)),
                ("buy_in", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=11)),
                ("cash_out", models.DecimalField(blank=True, decimal_places=2, max_digits=11, null=True)),
                ("notes", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resumed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("accumulated_seconds", models.PositiveBigIntegerField(default=0)),
                ("status", models.CharField(choices=[("running", "Running"), ("paused", "Paused"), ("finished", "Finished")], default="running", max_length=8)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="poker_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at", "-pk"]},
        ),
    ]
