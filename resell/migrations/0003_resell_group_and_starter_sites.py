from django.db import migrations

STARTER_SITES = ["eBay", "Poshmark", "Mercari", "Depop", "Facebook Marketplace", "Etsy", "In person"]


def create_group_and_sites(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name="Resell")
    Platform = apps.get_model("resell", "Platform")
    for name in STARTER_SITES:
        if not Platform.objects.filter(name__iexact=name).exists():
            Platform.objects.create(name=name)


def remove_unused_starter_sites(apps, schema_editor):
    Platform = apps.get_model("resell", "Platform")
    Platform.objects.filter(name__in=STARTER_SITES, items__isnull=True, listings__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("resell", "0002_listings_returns_receipts"),
    ]

    operations = [migrations.RunPython(create_group_and_sites, remove_unused_starter_sites)]
