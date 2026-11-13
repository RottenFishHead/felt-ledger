from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from .models import Expense, Item, ItemImage, Listing

# Uploaded files that should be removed from storage along with (or when replaced on) their row.
FILE_FIELDS = {ItemImage: "image", Expense: "receipt"}


def _delete_file(field_file):
    if field_file and field_file.name:
        field_file.storage.delete(field_file.name)


@receiver(post_delete, sender=ItemImage)
@receiver(post_delete, sender=Expense)
def delete_file_with_row(sender, instance, **kwargs):
    # post_delete also fires for cascade deletes (e.g. deleting an Item deletes its images).
    _delete_file(getattr(instance, FILE_FIELDS[sender]))


@receiver(pre_save, sender=ItemImage)
@receiver(pre_save, sender=Expense)
def delete_replaced_file(sender, instance, **kwargs):
    if not instance.pk:
        return
    field = FILE_FIELDS[sender]
    old = sender.objects.filter(pk=instance.pk).values_list(field, flat=True).first()
    if old and old != getattr(instance, field).name:
        getattr(instance, field).storage.delete(old)


@receiver(post_save, sender=Listing)
@receiver(post_delete, sender=Listing)
def update_item_status(sender, instance, **kwargs):
    item = Item.objects.filter(pk=instance.item_id).first()
    if item:
        item.refresh_status()
