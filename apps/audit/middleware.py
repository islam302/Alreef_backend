from django.utils.deprecation import MiddlewareMixin

# High-noise / non-business-critical mutation paths that don't warrant an
# audit row on every call (cart edits, auth token refresh, event tracking).
EXCLUDED_PATH_FRAGMENTS = ("/auth/", "/cart/", "/token/refresh/", "/events/track/", "/webhooks/")

AUDITED_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class AuditLogMiddleware(MiddlewareMixin):
    """
    Lightweight request-level audit trail for API-driven mutations (spec
    §96) that don't pass through Django admin (which already gets LogEntry
    rows automatically). Business-critical operations additionally call
    apps.audit.services.record() directly with richer context (e.g. order
    status transitions, refunds) — this middleware is the coarse safety net.
    """

    def process_response(self, request, response):
        if not request.path.startswith("/api/v1/"):
            return response
        if request.method not in AUDITED_METHODS:
            return response
        if response.status_code >= 400:
            return response
        if any(fragment in request.path for fragment in EXCLUDED_PATH_FRAGMENTS):
            return response

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return response

        from apps.audit.models import AuditAction
        from apps.audit.services import record

        record(actor=user, action=AuditAction.API_MUTATION, model_name="", object_id="", changes={}, request=request)
        return response
