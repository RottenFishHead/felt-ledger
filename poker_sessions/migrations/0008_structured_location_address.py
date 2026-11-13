from django.db import migrations, models
from pokertracker.addresses import split_address

ADDRESS_FIELDS = ("street_address", "address_line_2", "city", "state", "postal_code")


def split_existing_addresses(apps, schema_editor):
    Location = apps.get_model("poker_sessions", "Location")
    for location in Location.objects.exclude(address=""):
        for field, value in split_address(location.address).items():
            setattr(location, field, value)
        location.save(update_fields=ADDRESS_FIELDS)


def join_addresses(apps, schema_editor):
    Location = apps.get_model("poker_sessions", "Location")
    for location in Location.objects.all():
        state_zip = " ".join(p for p in (location.state, location.postal_code) if p)
        parts = (location.street_address, location.address_line_2, location.city, state_zip)
        location.address = ", ".join(p for p in parts if p)
        location.save(update_fields=["address"])


class Migration(migrations.Migration):
    dependencies = [
        ("poker_sessions", "0007_allow_null_resumed_at"),
    ]

    operations = [
        migrations.AddField("location", "street_address", models.CharField(blank=True, max_length=255)),
        migrations.AddField("location", "address_line_2",
                            models.CharField(blank=True, help_text="Suite, unit, etc.", max_length=255)),
        migrations.AddField("location", "city", models.CharField(blank=True, max_length=100)),
        migrations.AddField("location", "state", models.CharField(blank=True, max_length=50)),
        migrations.AddField("location", "postal_code", models.CharField(blank=True, max_length=20)),
        migrations.AddField("location", "country", models.CharField(blank=True, default="United States", max_length=60)),
        migrations.RunPython(split_existing_addresses, join_addresses),
        migrations.RemoveField("location", "address"),
    ]
