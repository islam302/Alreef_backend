def record(*, actor=None, action: str, model_name: str = "", object_id="", changes=None, request=None):
    from apps.audit.models import AuditLog

    ip_address, path, method = None, "", ""
    if request is not None:
        ip_address = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        path = request.path
        method = request.method

    AuditLog.objects.create(
        actor=actor, action=action, model_name=model_name, object_id=str(object_id), changes=changes or {},
        ip_address=ip_address, path=path, method=method,
    )
