from functools import wraps
from django.core.exceptions import PermissionDenied


def main_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_main_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped

