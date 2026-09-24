import logging

from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import error_response

logger = logging.getLogger(__name__)

_DRF_CODE_MAP = {
    drf_exceptions.NotAuthenticated: ErrorCodes.AUTH_TOKEN_INVALID,
    drf_exceptions.AuthenticationFailed: ErrorCodes.AUTH_INVALID_CREDENTIALS,
    drf_exceptions.PermissionDenied: ErrorCodes.PERMISSION_DENIED,
    drf_exceptions.Throttled: ErrorCodes.THROTTLED,
    drf_exceptions.MethodNotAllowed: ErrorCodes.METHOD_NOT_ALLOWED,
    Http404: ErrorCodes.NOT_FOUND,
    drf_exceptions.NotFound: ErrorCodes.NOT_FOUND,
}


def custom_exception_handler(exc, context):
    """Converts every exception into the standard {success, message, data, errors} envelope."""

    if isinstance(exc, ApplicationError):
        return error_response(message=exc.message, code=exc.code, errors=exc.details, status=exc.status_code)

    response = drf_exception_handler(exc, context)

    if response is None:
        logger.exception("Unhandled exception in %s", context.get("view"), exc_info=exc)
        return error_response(
            message="An unexpected error occurred.",
            code=ErrorCodes.INTERNAL_ERROR,
            status=500,
        )

    code = _DRF_CODE_MAP.get(type(exc), ErrorCodes.VALIDATION_ERROR)

    if isinstance(exc, drf_exceptions.ValidationError):
        return error_response(
            message="Validation failed.",
            errors=response.data,
            code=ErrorCodes.VALIDATION_ERROR,
            status=response.status_code,
        )

    detail = response.data.get("detail", response.data) if isinstance(response.data, dict) else response.data
    return error_response(message=str(detail), code=code, status=response.status_code)
