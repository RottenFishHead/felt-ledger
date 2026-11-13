from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from pokertracker.addresses import AddressFields


class Location(AddressFields):
    RATING_CHOICES = [(rating, f"{rating} star{'s' if rating != 1 else ''}") for rating in range(1, 6)]

    name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, default=3)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class PokerSession(models.Model):
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    STATUS_CHOICES = [(RUNNING, "Running"), (PAUSED, "Paused"), (FINISHED, "Finished")]
    GAME_CHOICES = [("No-Limit Hold'em", "No-Limit Hold'em"), ("Pot-Limit Omaha", "Pot-Limit Omaha"),("Limit Hold'em", "Limit Hold'em")]
    STAKES_CHOICES = (("$1/$2", "$1/$2"), ("$1/$3", "$1/$3"), ("$2/3", "$2/3"),("$2/$5", "$2/$5"), ("$5/$5", "$5/$5"), ("$5/10", "$5/$10"))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="poker_sessions")
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    game = models.CharField(max_length=80, choices=GAME_CHOICES, default="No-Limit Hold'em")
    stakes = models.CharField(max_length=40, blank=True, choices=STAKES_CHOICES, default="$2/$5")
    buy_in = models.DecimalField(max_digits=11, decimal_places=2, default=Decimal("0.00"))
    cash_out = models.DecimalField(max_digits=11, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    resumed_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    accumulated_seconds = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=RUNNING)

    class Meta:
        ordering = ["-started_at", "-pk"]

    def __str__(self):
        return f"Session #{self.pk}"

    def get_absolute_url(self):
        return reverse("session-detail", kwargs={"pk": self.pk})

    @property
    def profit(self):
        if self.cash_out is None or self.status != self.FINISHED:
            return None
        return self.cash_out - self.buy_in

    @property
    def elapsed_seconds(self):
        secs = self.accumulated_seconds
        if self.status == self.RUNNING and self.resumed_at:
            secs += max(0, int((timezone.now() - self.resumed_at).total_seconds()))
        return secs

    def clean(self):
        if self.buy_in is not None and self.buy_in < 0:
            raise ValidationError({"buy_in": "Buy-in must be zero or greater."})
        if self.cash_out is not None and self.cash_out < 0:
            raise ValidationError({"cash_out": "Cash-out must be zero or greater."})
