from decimal import Decimal
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Sum, Q, F
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView
from .forms import SessionEditForm, SessionForm
from .models import Location, PokerSession


class OwnedSessionsMixin(LoginRequiredMixin):
    model = PokerSession

    def get_queryset(self):
        return PokerSession.objects.filter(user=self.request.user)


def hourly_rate_for(profit, total_seconds):
    if not total_seconds:
        return Decimal("0.00")
    return (profit * Decimal("3600") / Decimal(total_seconds)).quantize(Decimal("0.01"))


def completed_session_stats(sessions):
    totals = sessions.aggregate(
        total_in=Sum("buy_in"),
        total_out=Sum("cash_out"),
        total_seconds=Sum("accumulated_seconds"),
        wins=Count("id", filter=Q(cash_out__gt=F("buy_in"))),
        losses=Count("id", filter=Q(cash_out__lt=F("buy_in"))),
    )
    completed_count = sessions.count()
    wins = totals["wins"] or 0
    losses = totals["losses"] or 0
    profit = (totals["total_out"] or Decimal("0")) - (totals["total_in"] or Decimal("0"))
    total_seconds = totals["total_seconds"] or 0
    hourly_rate = hourly_rate_for(profit, total_seconds)
    return {
        "profit": profit,
        "hourly_rate": hourly_rate,
        "wins": wins,
        "losses": losses,
        "break_even": completed_count - wins - losses,
        "win_rate": round(wins / completed_count * 100, 1) if completed_count else 0,
        "completed_count": completed_count,
    }


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "sessions/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = PokerSession.objects.filter(user=self.request.user)
        finished = qs.filter(status=PokerSession.FINISHED, cash_out__isnull=False)
        current_month = timezone.localtime().date()
        monthly_finished = finished.filter(
            started_at__year=current_month.year,
            started_at__month=current_month.month,
        )
        monthly_totals = monthly_finished.aggregate(
            total_in=Sum("buy_in"),
            total_out=Sum("cash_out"),
        )
        overall_stats = completed_session_stats(finished)
        ctx.update(
            profit=overall_stats["profit"],
            hourly_rate=overall_stats["hourly_rate"],
            wins=overall_stats["wins"],
            losses=overall_stats["losses"],
            completed_count=overall_stats["completed_count"],
            won_this_month=(monthly_totals["total_out"] or Decimal("0"))
            - (monthly_totals["total_in"] or Decimal("0")),
            active=qs.filter(status__in=[PokerSession.RUNNING, PokerSession.PAUSED]).first(),
            recent=qs[:7],
        )
        cumulative = Decimal("0")
        points = []
        for session in finished.order_by("started_at", "pk"):
            cumulative += session.profit
            points.append({"date": timezone.localtime(session.started_at).strftime("%b %d"), "value": float(cumulative)})
        ctx["chart_points"] = points
        return ctx


class SessionListView(OwnedSessionsMixin, ListView):
    template_name = "sessions/session_list.html"
    context_object_name = "sessions"
    paginate_by = 20

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        finished = self.get_queryset().filter(status=PokerSession.FINISHED, cash_out__isnull=False)
        ctx.update(**completed_session_stats(finished))
        return ctx


class SessionStatisticsView(LoginRequiredMixin, TemplateView):
    template_name = "sessions/statistics.html"
    mode = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sessions = PokerSession.objects.filter(user=self.request.user)
        selected = self.request.GET.get("selection", "")
        choices = []
        results = sessions.none()
        selection_label = ""
        invalid_selection = False

        if self.mode == "month":
            months = sessions.dates("started_at", "month", order="DESC")
            choices = [
                (month.strftime("%Y-%m"), month.strftime("%B %Y"))
                for month in months
            ]
            selection_label = "Month"
            if selected:
                try:
                    year, month = map(int, selected.split("-"))
                    if len(selected) != 7 or not 1 <= month <= 12:
                        raise ValueError
                    if selected not in dict(choices):
                        raise ValueError
                except ValueError:
                    invalid_selection = True
                else:
                    results = sessions.filter(started_at__year=year, started_at__month=month)
        elif self.mode == "year":
            years = sessions.dates("started_at", "year", order="DESC")
            choices = [(str(year.year), str(year.year)) for year in years]
            selection_label = "Year"
            if selected:
                if selected not in dict(choices):
                    invalid_selection = True
                else:
                    results = sessions.filter(started_at__year=int(selected))
        else:
            choices = list(PokerSession.STAKES_CHOICES)
            selection_label = "Stakes"
            if selected:
                valid_stakes = {value for value, _ in choices}
                if selected not in valid_stakes:
                    invalid_selection = True
                else:
                    results = sessions.filter(stakes=selected)

        completed = results.filter(status=PokerSession.FINISHED, cash_out__isnull=False)
        ctx.update(
            mode=self.mode,
            selection_label=selection_label,
            selection_choices=choices,
            selected=selected,
            has_selection=bool(selected) and not invalid_selection,
            invalid_selection=invalid_selection,
            sessions=results,
            **completed_session_stats(completed),
        )
        return ctx


class MonthStatisticsView(SessionStatisticsView):
    mode = "month"


class YearStatisticsView(SessionStatisticsView):
    mode = "year"


class LimitStatisticsView(SessionStatisticsView):
    mode = "limit"


class SessionDetailView(OwnedSessionsMixin, DetailView):
    template_name = "sessions/session_detail.html"


class SessionLocationMixin:
    def form_valid(self, form):
        with transaction.atomic():
            location_name = form.cleaned_data["new_location_name"]
            if location_name:
                form.instance.location = Location.objects.create(
                    name=location_name,
                    street_address=form.cleaned_data["new_location_street"],
                    city=form.cleaned_data["new_location_city"],
                    state=form.cleaned_data["new_location_state"],
                    postal_code=form.cleaned_data["new_location_postal_code"],
                    rating=form.cleaned_data["new_location_rating"] or 3,
                )
            time_played = form.cleaned_data.get("time_played")
            if time_played is not None:
                form.instance.accumulated_seconds = time_played
                if form.instance.status == PokerSession.RUNNING:
                    form.instance.resumed_at = timezone.now()
            return super().form_valid(form)


class SessionCreateView(SessionLocationMixin, OwnedSessionsMixin, CreateView):
    form_class = SessionForm
    template_name = "sessions/session_form.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.status = PokerSession.RUNNING
        form.instance.started_at = timezone.now()
        form.instance.resumed_at = form.instance.started_at
        return super().form_valid(form)


class SessionUpdateView(SessionLocationMixin, OwnedSessionsMixin, UpdateView):
    form_class = SessionEditForm
    template_name = "sessions/session_form.html"


class SessionDeleteView(OwnedSessionsMixin, DeleteView):
    template_name = "sessions/session_confirm_delete.html"
    success_url = reverse_lazy("session-list")


class SessionActionView(LoginRequiredMixin, View):
    """Server-authoritative timer transitions; accepts POST only."""
    http_method_names = ["post"]

    def post(self, request, pk, action):
        if action not in ("pause", "resume", "finish"):
            return HttpResponseBadRequest("Unknown action")
        with transaction.atomic():
            session = get_object_or_404(
                PokerSession.objects.select_for_update(),
                pk=pk, user=request.user,
            )
            now = timezone.now()
            if action == "pause" and session.status == PokerSession.RUNNING:
                session.accumulated_seconds += max(0, int((now - session.resumed_at).total_seconds()))
                session.resumed_at = None
                session.status = PokerSession.PAUSED
            elif action == "resume" and session.status == PokerSession.PAUSED:
                session.resumed_at = now
                session.status = PokerSession.RUNNING
            elif action == "finish" and session.status in (PokerSession.RUNNING, PokerSession.PAUSED):
                cash_text = request.POST.get("cash_out", "").strip()
                try:
                    cash_out = Decimal(cash_text)
                except Exception:
                    return HttpResponseBadRequest("Enter a valid cash-out amount.")
                if not cash_out.is_finite() or cash_out < 0 or cash_out.as_tuple().exponent < -2 or cash_out > Decimal("999999999.99"):
                    return HttpResponseBadRequest("Enter a nonnegative cash-out amount (2 decimals maximum).")
                if session.status == PokerSession.RUNNING:
                    session.accumulated_seconds += max(0, int((now - session.resumed_at).total_seconds()))
                session.resumed_at = None
                session.ended_at = now
                session.cash_out = cash_out
                session.status = PokerSession.FINISHED
            else:
                return HttpResponseBadRequest("Invalid session state for this action.")
            session.save()
        return redirect(session)


class SignUpView(FormView):
    template_name = "registration/signup.html"
    form_class = UserCreationForm
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return super().form_valid(form)


class LocationStatisticsView(LoginRequiredMixin, TemplateView):
    """Wins/losses per location: a ranking of every room, or one room's detail via ?location=<pk|none>."""
    template_name = "sessions/location_statistics.html"
    NO_LOCATION = "none"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sessions = PokerSession.objects.filter(user=self.request.user)
        completed = sessions.filter(status=PokerSession.FINISHED, cash_out__isnull=False)
        selected = self.request.GET.get("location", "")
        ctx.update(mode="location", selected=selected, invalid_selection=False, location=None)

        if not selected:
            ctx["rows"] = self.location_rows(completed)
            return ctx

        if selected == self.NO_LOCATION:
            results = sessions.filter(location__isnull=True)
        else:
            location = Location.objects.filter(pk=selected).first() if selected.isdigit() else None
            if location is None or not sessions.filter(location=location).exists():
                ctx["invalid_selection"] = True
                return ctx
            ctx["location"] = location
            results = sessions.filter(location=location)
        ctx.update(
            sessions=results,
            **completed_session_stats(completed.filter(pk__in=results)),
        )
        return ctx

    def location_rows(self, completed):
        grouped = (
            completed.values("location")
            .annotate(
                completed_count=Count("id"),
                wins=Count("id", filter=Q(cash_out__gt=F("buy_in"))),
                losses=Count("id", filter=Q(cash_out__lt=F("buy_in"))),
                total_in=Sum("buy_in"),
                total_out=Sum("cash_out"),
                total_seconds=Sum("accumulated_seconds"),
            )
        )
        locations = Location.objects.in_bulk([g["location"] for g in grouped if g["location"]])
        rows = []
        for group in grouped:
            profit = group["total_out"] - group["total_in"]
            count = group["completed_count"]
            rows.append({
                "location": locations.get(group["location"]),
                "key": group["location"] or self.NO_LOCATION,
                "completed_count": count,
                "wins": group["wins"],
                "losses": group["losses"],
                "break_even": count - group["wins"] - group["losses"],
                "win_rate": round(group["wins"] / count * 100, 1),
                "profit": profit,
                "hourly_rate": hourly_rate_for(profit, group["total_seconds"]),
            })
        rows.sort(key=lambda row: row["profit"], reverse=True)
        return rows
