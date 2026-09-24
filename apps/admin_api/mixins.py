"""
Shared plumbing for every staff-facing endpoint.

The storefront API answers in the envelope produced by
`apps.core.response.success_response`. DRF's generic viewsets answer with a
bare body, so these bases re-wrap each generic action. Doing it here once
keeps every admin route byte-compatible with the rest of /api/v1/.

Why three bases instead of one ModelViewSet
-------------------------------------------
Several resources are read-only but still need POST or PATCH for a custom
`@action` (moving an order's status, adjusting stock). Widening
`http_method_names` on a ModelViewSet to allow those verbs *also* exposes the
generic `create`/`update` routes the router builds from the same URL — which
would let staff POST a brand new Order or Payment into existence.

The router only maps a verb when the method actually exists on the viewset
(`hasattr` check in `SimpleRouter.get_method_map`). So the capability is
decided by which base a viewset inherits, not by an allowlist that is easy to
widen by accident.
"""
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.core.pagination import StandardResultsPagination
from apps.core.response import success_response


class AuditedMixin:
    """Writes an AuditLog row for every successful mutation."""

    audit_model_name = ""

    def _audit(self, action: str, instance, changes=None):
        record(
            actor=self.request.user,
            action=action,
            model_name=self.audit_model_name or instance.__class__.__name__,
            object_id=getattr(instance, "pk", ""),
            changes=changes or {},
            request=self.request,
        )


class ReadOnlyEnvelopeViewSet(AuditedMixin, viewsets.GenericViewSet):
    """
    List + retrieve in the project envelope, and nothing else.

    Deliberately not a ReadOnlyModelViewSet: custom `@action`s still work,
    but no amount of `http_method_names` fiddling can conjure a create or
    delete route, because those methods do not exist here.
    """

    permission_classes = [IsAdminUser]
    pagination_class = StandardResultsPagination

    # A separate serializer for list rows keeps index responses cheap; falls
    # back to the detail serializer when a viewset does not define one.
    list_serializer_class = None

    def get_serializer_class(self):
        if self.action == "list" and self.list_serializer_class is not None:
            return self.list_serializer_class
        return super().get_serializer_class()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return success_response(self.get_serializer(queryset, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


class DeletableEnvelopeViewSet(ReadOnlyEnvelopeViewSet):
    """Read plus delete — for records staff may remove but never author,
    such as a customer's review."""

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self._audit(AuditAction.DELETE, instance)
        instance.delete()
        return success_response(message="Deleted.", status=200)


class EnvelopeViewSet(DeletableEnvelopeViewSet):
    """Full CRUD in the project envelope."""

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        self._audit(AuditAction.CREATE, instance, {"created": serializer.data})
        return success_response(serializer.data, message="Created.", status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        self._audit(AuditAction.UPDATE, instance, {"updated": list(serializer.validated_data.keys())})
        return success_response(serializer.data, message="Saved.")

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, partial=True, **kwargs)
