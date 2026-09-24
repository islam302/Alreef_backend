from rest_framework.response import Response


def success_response(data=None, message: str = "Success", status: int = 200, meta: dict | None = None):
    payload = {"success": True, "message": message, "data": data, "errors": None}
    if meta is not None:
        payload["meta"] = meta
    return Response(payload, status=status)


def error_response(message: str = "Error", errors=None, status: int = 400, code: str | None = None):
    """errors: field-level validation dict, or None for single business errors (use `code`)."""
    error_body = errors if errors is not None else {}
    if code:
        error_body = {"code": code, "fields": errors or {}}
    return Response({"success": False, "message": message, "data": None, "errors": error_body}, status=status)
