# Task: Add SKU + bin-location logic to the existing inventory form

The add-item form already exists. Do NOT rebuild the UI. Add the logic behind it.

## Step 0 — Look before building

1. Find the existing add-item form, the item data model/storage, and how saves happen.
2. Summarize what you found (framework, storage, item fields, status values) and
   propose a plan. Wait for my OK before writing code.
3. If anything below conflicts with the existing structure, ask; don't guess.

## Concepts (important)

- LOCATION = physical slot, e.g. "A16" (bin letter + 2-digit slot). Locations are REUSED.
- ITEM NUMBER = global counter (1001, 1002, ...). NEVER reused, never decremented.
- SKU = `{LOCATION}-{ITEM NUMBER}`, e.g. "A16-1047". Unique forever. NEVER reused.
- A location is "occupied" while an item is physically in it; "open" otherwise.

## Data needed (add only what's missing)

Item fields: sku (unique), item_number (unique int), location, status,
location_released_at (nullable), shipped_at (nullable).
Status values: draft, listed, sold, shipped, returned, removed.
Settings/config:

- next_item_number (start 1001; if items already exist, set to max + 1)
- default_bin_capacity = 25
- per-bin capacity overrides, e.g. { "K": 12 } for outerwear bins
- disabled bins list (bins I'm not using)

## Bin sequence

A, B, C ... Z, then AA, AB ... AZ, BA ... (spreadsheet-column style).
Slots are 01..capacity, always zero-padded to 2 digits ("A01", "A16").

## Slot assignment rule

Next location = the LOWEST open slot, scanning bins in sequence order,
slots 01..capacity, skipping disabled bins. This fills gaps left by shipped items
before starting new bins.

## Form behavior

On form open:

- Compute next open location + next item number.
- Show SKU preview (e.g. "A16-1047") in the SKU field, and the location.
- This is a PREVIEW only: do not increment the counter or reserve anything.
- SKU field is read-only.
- Location field is editable (override if item doesn't fit). If I change the
    location, update the SKU preview to match. Validate the override: the slot
    must exist, be within capacity, and be open.
- Add a "Bin full → next bin" button that jumps to the first open slot in the
    next bin.

On save:

- In ONE atomic operation (transaction/lock, whatever the storage supports):
    1. Re-check the chosen location is still open. If taken (e.g. another device
       saved first), pick the next open slot and tell me it changed.
    2. Assign item_number = next_item_number, then increment the counter.
    3. Build sku = `${location}-${item_number}`.
    4. Copy location into the item's location field. Save the item.
- Closing the form without saving must not consume a number or a slot.

## Status → location rules

- sold: item still physically in the bin → location stays OCCUPIED.
- shipped: set shipped_at, set location_released_at = now → slot becomes OPEN
  immediately. Keep the item's sku and location fields as history; don't clear them.
- returned: item is back. Assign a NEW open location (lowest open slot) and update
  the location field. The SKU does NOT change (it keeps the original prefix; that's
  fine, it's a permanent ID). Show the new location prominently so I re-tag/shelve it.
  Store previous locations in a history field/log if easy.
- removed (donated/trashed/personal): release the location like shipped.

"Occupied" = any item whose location equals the slot AND whose status is
draft, listed, sold, or returned AND location_released_at is null.

## Edge cases to handle

- All configured bins full → generate next bin in sequence (or warn if bins are
  disabled/capped; ask me which behavior I want).
- Lowering a bin's capacity below occupied slots: don't move items, just stop
  assigning slots above the new capacity.
- Duplicate SKU on save must be impossible (unique constraint if storage supports it).
- Existing items with old/blank SKUs: don't rewrite them; ask me before any migration.

## Tests / verification

Write tests (or a test script) covering:

- First item → A01-1001; 26th item with capacity 25 → B01-1026
- Ship A05 → next new item gets A05 with a new, higher item number
- Abandoned form doesn't consume a number
- Two saves racing for the same slot → different slots, no duplicate SKU
- Return → new location, same SKU
- Per-bin capacity override respected; Z → AA rollover

When done, give me a short summary of what changed and how to use the
"Bin full" and capacity settings.
