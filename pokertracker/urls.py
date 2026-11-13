from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from resell.access import can_use_resell
from poker_sessions.views import SignUpView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/signup/", SignUpView.as_view(), name="signup"),
    path("resell/", include("resell.urls")),
    path("", include("poker_sessions.urls")),
]


@login_required
def protected_media(request, path):
    """Uploaded files are served by Django (also in production), whether they're stored on the volume or in R2.
    Every upload is a resell photo or receipt, so each one needs resell access."""
    if not can_use_resell(request.user):
        raise PermissionDenied
    if not path or not default_storage.exists(path):
        raise Http404
    response = FileResponse(default_storage.open(path))
    response["Cache-Control"] = "private, max-age=86400"  # file names are never reused, so a day is safe
    return response


urlpatterns.insert(0, re_path(rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$", protected_media))
