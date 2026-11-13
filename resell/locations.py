"""Bin slots and SKUs.

A location is a bin plus a two-digit slot, e.g. "A16". Bins run A…Z, AA…AZ, BA… like spreadsheet columns.
A slot is occupied while an item that isn't written off sits in it (location_released_at is empty).
A SKU is "{location}-{item_number}" and is fixed once the item is saved, even if the item moves.
"""
import re
from django.db import transaction
from django.utils import timezone
from .models import InventorySettings, Item

SLOT_RE = re.compile(r"^([A-Z]+)(\d{1,2})$")
BIN_RE = re.compile(r"^[A-Z]+$")


def bin_name(index):
    """0 → A, 25 → Z, 26 → AA, 27 → AB …"""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def bin_index(name):
    index = 0
    for letter in name:
        index = index * 26 + ord(letter) - 64
    return index - 1


def is_bin_name(value):
    return isinstance(value, str) and bool(BIN_RE.match(value))


def parse_slot(text):
    """"a5" or "A05" → ("A", 5). Returns None for anything that isn't a bin slot, e.g. "Closet shelf"."""
    match = SLOT_RE.match((text or "").strip().upper())
    return (match[1], int(match[2])) if match else None


def slot_label(bin_, slot):
    return f"{bin_}{slot:02d}"


def normalize(text):
    """"a5" → "A05"; text that isn't a slot is returned trimmed but otherwise unchanged."""
    parsed = parse_slot(text)
    return slot_label(*parsed) if parsed else (text or "").strip()


def occupied_slots(exclude_pk=None):
    items = (Item.objects.filter(location_released_at__isnull=True).exclude(status=Item.WRITTEN_OFF)
             .exclude(storage_location=""))
    if exclude_pk:
        items = items.exclude(pk=exclude_pk)
    return {normalize(location) for location in items.values_list("storage_location", flat=True)
            if parse_slot(location)}


def first_open_slot(config, occupied, start_bin=0):
    """Lowest open slot from bin number start_bin on. New bins are added forever, so this always finds one."""
    disabled = set(config.disabled_bins)
    index = start_bin
    while True:
        name = bin_name(index)
        if name not in disabled:
            for slot in range(1, config.capacity(name) + 1):
                label = slot_label(name, slot)
                if label not in occupied:
                    return label
        index += 1


def slot_problem(location, config, occupied):
    """Why this location can't be used, or None if it's an open slot."""
    parsed = parse_slot(location)
    if not parsed:
        return "Use a bin letter and slot number, e.g. “A16”."
    name, slot = parsed
    if name in config.disabled_bins:
        return f"Bin {name} is switched off in the bin settings."
    capacity = config.capacity(name)
    if not 1 <= slot <= capacity:
        return f"Bin {name} has slots 01 to {capacity:02d}."
    if slot_label(name, slot) in occupied:
        return f"Something is already in {slot_label(name, slot)}."
    return None


def next_open_location(exclude_pk=None):
    return first_open_slot(InventorySettings.load(), occupied_slots(exclude_pk))


def next_bin_location(current, exclude_pk=None):
    """The first open slot in the bin after the current one ("Bin full → next bin")."""
    parsed = parse_slot(current)
    start = bin_index(parsed[0]) + 1 if parsed else 0
    return first_open_slot(InventorySettings.load(), occupied_slots(exclude_pk), start)


def preview(location, config=None):
    """The SKU a new item would get right now. Nothing is reserved."""
    config = config or InventorySettings.load()
    return f"{normalize(location)}-{config.next_item_number}"


def save_new_item(item, requested_location):
    """Give a new item its location, item number and SKU, and save it — all in one transaction.

    If the requested slot was taken meanwhile (or none was asked for) the lowest open slot is used instead.
    Returns the location used. The caller compares it with what was asked for to tell the user.
    """
    with transaction.atomic():  # SQLite takes the write lock here (transaction_mode IMMEDIATE)
        config = InventorySettings.load()
        config = InventorySettings.objects.select_for_update().get(pk=config.pk)
        occupied = occupied_slots()
        location = normalize(requested_location) if requested_location else ""
        if not location or slot_problem(location, config, occupied):
            location = first_open_slot(config, occupied)
        item.item_number = config.next_item_number
        item.sku = f"{location}-{item.item_number}"
        item.storage_location = location
        item.location_released_at = None
        config.next_item_number += 1
        config.save(update_fields=["next_item_number"])
        item.save()
    return location


def move(item, location):
    """Put an item in a new slot, remembering the old one. Doesn't save."""
    if item.storage_location and normalize(item.storage_location) != location:
        item.location_history = [*item.location_history,
                                 {"location": item.storage_location, "until": timezone.now().isoformat()}]
    item.storage_location = location
    item.location_released_at = None


def release(item):
    """The item left its slot (shipped, donated…): the slot is open again. The location is kept as history."""
    if item.location_released_at is None:
        item.location_released_at = timezone.now()


def restock(item, keep_slot_if_open=False):
    """The item is back on the shelf: give it the lowest open slot. Doesn't save; call inside a transaction.

    With keep_slot_if_open, an item whose old slot is still free (e.g. an undo) goes straight back there.
    Returns the new location.
    """
    config = InventorySettings.load()
    occupied = occupied_slots(exclude_pk=item.pk)
    current = normalize(item.storage_location)
    if keep_slot_if_open and parse_slot(current) and not slot_problem(current, config, occupied):
        location = current
    else:
        location = first_open_slot(config, occupied)
    move(item, location)
    return location
