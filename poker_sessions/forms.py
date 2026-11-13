from django import forms
from .models import Location, PokerSession


class SessionForm(forms.ModelForm):
    NEW_LOCATION_ADDRESS_FIELDS = ("new_location_street", "new_location_city", "new_location_state", "new_location_postal_code")

    new_location_name = forms.CharField(max_length=120, required=False, label="Location name")
    new_location_street = forms.CharField(max_length=255, required=False, label="Street address")
    new_location_city = forms.CharField(max_length=100, required=False, label="City")
    new_location_state = forms.CharField(max_length=50, required=False, label="State")
    new_location_postal_code = forms.CharField(max_length=20, required=False, label="ZIP code")
    new_location_rating = forms.ChoiceField(
        choices=Location.RATING_CHOICES,
        required=False,
        label="Rating",
    )

    class Meta:
        model = PokerSession
        fields = ["location", "game", "stakes", "buy_in", "notes"]
        widgets = {
            "game": forms.Select(),
            "stakes": forms.Select(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        new_name = cleaned.get("new_location_name", "").strip()
        for field in self.NEW_LOCATION_ADDRESS_FIELDS:
            cleaned[field] = cleaned.get(field, "").strip()
        has_address = any(cleaned[field] for field in self.NEW_LOCATION_ADDRESS_FIELDS)
        if new_name and cleaned.get("location"):
            self.add_error("location", "Choose a saved location or enter a new one, not both.")
        elif not new_name and has_address:
            self.add_error("new_location_name", "Enter a location name to save this address.")
        cleaned["new_location_name"] = new_name
        return cleaned


class SessionEditForm(SessionForm):
    time_played = forms.CharField(
        required=False,
        label="Time played",
        help_text="Correct the elapsed time in hours:minutes:seconds.",
        widget=forms.TextInput(attrs={"placeholder": "HH:MM:SS", "inputmode": "numeric"}),
    )

    class Meta:
        model = SessionForm.Meta.model
        fields = ["location", "game", "stakes", "buy_in", "cash_out", "notes"]
        widgets = SessionForm.Meta.widgets

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            total_seconds = self.instance.elapsed_seconds
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.initial["time_played"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def clean_time_played(self):
        value = self.cleaned_data["time_played"].strip()
        if not value:
            return None
        parts = value.split(":")
        if (
            len(parts) != 3
            or not parts[0].isdigit()
            or len(parts[1]) != 2
            or len(parts[2]) != 2
            or not parts[1].isdigit()
            or not parts[2].isdigit()
            or int(parts[1]) > 59
            or int(parts[2]) > 59
        ):
            raise forms.ValidationError("Enter time as hours:minutes:seconds, for example 02:15:30.")
        hours, minutes, seconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds

    def clean(self):
        cleaned = super().clean()
        if self.instance.status == PokerSession.FINISHED and cleaned.get("cash_out") is None:
            self.add_error("cash_out", "A completed session needs a cash-out amount.")
        return cleaned
