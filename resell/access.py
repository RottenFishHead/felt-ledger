from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

RESELL_GROUP = "Resell"


def can_use_resell(user):
    """Superusers and members of the "Resell" group (added in the admin) can use the resell pages."""
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=RESELL_GROUP).exists()


def resell_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_use_resell(request.user):
            raise PermissionDenied("Ask the site owner to add your account to the Resell group.")
        return view(request, *args, **kwargs)
    return wrapper


def resell_access(request):
    """Template context processor: lets the menu show the Poker/Resell switch only to people who can use it."""
    user = getattr(request, "user", None)
    return {"can_use_resell": user is not None and can_use_resell(user)}
