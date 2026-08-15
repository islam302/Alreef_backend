"""
Idempotency-Key support for unsafe POST endpoints (checkout, payment
creation, refunds, webhooks). See docs/api/errors.md and
docs/architecture/payments.md.

Usage in a view:

    from apps.core.idempotency import IdempotentRequestMixin

    class CreatePaymentView(IdempotentRequestMixin, APIView):
        idempotency_required = True
        def handle_idempotent_post(self, request, *args, **kwargs):
            ...  # business logic, return a Response
"""
import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from rest_framework.response import Response

from apps.core.error_codes import ErrorCodes
from apps.core.response import error_response

IDEMPOTENCY_HEADER = "Idempotency-Key"
_LOCK_SENTINEL = "__IN_PROGRESS__"


def _cache_key(scope: str, key: str) -> str:
    return f"idempotency:{scope}:{key}"


def _body_fingerprint(request) -> str:
    try:
        raw = json.dumps(request.data, sort_keys=True, default=str)
    except Exception:
        raw = str(request.body)
    return hashlib.sha256(raw.encode()).hexdigest()


class IdempotentRequestMixin:
    """
    Ensures a given Idempotency-Key + request body always returns the same
    stored response, and that concurrent duplicate requests do not both
    execute the underlying business logic (checkout / payment / refund).
    """

    idempotency_required = True
    idempotency_scope = None
    idempotency_ttl_seconds = None

    def post(self, request, *args, **kwargs):
        if not self.idempotency_required:
            return self.handle_idempotent_post(request, *args, **kwargs)

        key = request.headers.get(IDEMPOTENCY_HEADER)
        if not key:
            return error_response(
                message="Idempotency-Key header is required for this operation.",
                code=ErrorCodes.IDEMPOTENCY_KEY_REQUIRED,
                status=400,
            )

        scope = self.idempotency_scope or self.__class__.__name__
        cache_key = _cache_key(scope, key)
        fingerprint = _body_fingerprint(request)
        ttl = self.idempotency_ttl_seconds or settings.IDEMPOTENCY_KEY_TTL_SECONDS

        added = cache.add(cache_key, {"fingerprint": fingerprint, "status": _LOCK_SENTINEL}, timeout=ttl)

        if not added:
            stored = cache.get(cache_key)
            if stored is None:
                return error_response(
                    message="Duplicate request is already being processed. Retry shortly.",
                    code=ErrorCodes.IDEMPOTENCY_KEY_CONFLICT,
                    status=409,
                )
            if stored["fingerprint"] != fingerprint:
                return error_response(
                    message="Idempotency-Key was already used with a different request body.",
                    code=ErrorCodes.IDEMPOTENCY_KEY_CONFLICT,
                    status=409,
                )
            if stored["status"] == _LOCK_SENTINEL:
                return error_response(
                    message="Duplicate request is already being processed. Retry shortly.",
                    code=ErrorCodes.IDEMPOTENCY_KEY_CONFLICT,
                    status=409,
                )
            return Response(stored["body"], status=stored["status_code"])

        try:
            response: Response = self.handle_idempotent_post(request, *args, **kwargs)
        except Exception:
            cache.delete(cache_key)
            raise

        cache.set(
            cache_key,
            {"fingerprint": fingerprint, "status": response.status_code, "status_code": response.status_code, "body": response.data},
            timeout=ttl,
        )
        return response

    def handle_idempotent_post(self, request, *args, **kwargs):
        raise NotImplementedError
