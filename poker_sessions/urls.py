from django.urls import path
from .views import (
    DashboardView, SessionActionView, SessionCreateView, SessionDeleteView,
    LimitStatisticsView, LocationStatisticsView, MonthStatisticsView, SessionDetailView, SessionListView,
    SessionUpdateView, YearStatisticsView,
)
urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("sessions/", SessionListView.as_view(), name="session-list"),
    path("statistics/month/", MonthStatisticsView.as_view(), name="statistics-month"),
    path("statistics/year/", YearStatisticsView.as_view(), name="statistics-year"),
    path("statistics/limit/", LimitStatisticsView.as_view(), name="statistics-limit"),
    path("statistics/location/", LocationStatisticsView.as_view(), name="statistics-location"),
    path("sessions/new/", SessionCreateView.as_view(), name="session-create"),
    path("sessions/<int:pk>/", SessionDetailView.as_view(), name="session-detail"),
    path("sessions/<int:pk>/edit/", SessionUpdateView.as_view(), name="session-update"),
    path("sessions/<int:pk>/delete/", SessionDeleteView.as_view(), name="session-delete"),
    path("sessions/<int:pk>/<str:action>/", SessionActionView.as_view(), name="session-action"),
]
