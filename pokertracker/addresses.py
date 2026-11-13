from urllib.parse import quote_plus
from django.db import models


class AddressFields(models.Model):
    """Structured address plus Google Maps helpers. Subclasses need a `name` field."""

    street_address = models.CharField(max_length=255, blank=True)
    address_line_2 = models.CharField(max_length=255, blank=True, help_text="Suite, unit, etc.")
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=60, blank=True, default="United States")

    class Meta:
        abstract = True

    def _state_zip(self):
        return " ".join(part for part in (self.state, self.postal_code) if part)

    @property
    def full_address(self):
        """Single-line address, e.g. "123 Main St, Suite 4, Springfield, IL 62701, United States"."""
        parts = (self.street_address, self.address_line_2, self.city, self._state_zip(), self.country)
        return ", ".join(part for part in parts if part)

    @property
    def has_address(self):
        return bool(self.street_address or self.city or self.postal_code)

    def _map_query(self):
        # Line 2 (suite/unit) only confuses geocoding, so leave it out of map searches.
        parts = (self.name, self.street_address, self.city, self._state_zip(), self.country)
        return quote_plus(", ".join(part for part in parts if part))

    @property
    def map_url(self):
        """Link that opens the location in Google Maps (app on phones)."""
        if not self.has_address:
            return ""
        return f"https://www.google.com/maps/search/?api=1&query={self._map_query()}"

    @property
    def map_embed_url(self):
        """URL for an <iframe> map preview; no API key required."""
        if not self.has_address:
            return ""
        return f"https://maps.google.com/maps?q={self._map_query()}&output=embed"


def split_address(text):
    """Best-effort split of a free-text address like "1 Main St, Springfield, IL 62701" into fields."""
    parts = [part.strip() for part in (text or "").split(",") if part.strip()]
    fields = {}
    if not parts:
        return fields
    if len(parts) == 1:
        key = "street_address" if parts[0][:1].isdigit() else "city"
        fields[key] = parts[0]
        return fields
    fields["street_address"] = parts[0]
    if len(parts) == 2:
        fields["city"] = parts[1]
        return fields
    fields["city"] = parts[-2]
    if len(parts) > 3:
        fields["address_line_2"] = ", ".join(parts[1:-2])
    tail = parts[-1].split()
    if tail and tail[-1].replace("-", "").isdigit():
        fields["postal_code"] = tail.pop()
    fields["state"] = " ".join(tail)
    return fields
