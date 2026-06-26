from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware


class AllowAnyOriginCsrfViewMiddleware(CsrfViewMiddleware):
    def _origin_verified(self, request):
        if settings.CSRF_TRUST_ALL_ORIGINS:
            return True
        return super()._origin_verified(request)