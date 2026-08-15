from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwner(BasePermission):
    """Object-level permission: only the owning user (via `.user`) may access."""

    def has_object_permission(self, request, view, obj):
        owner = getattr(obj, "user", None)
        return owner is not None and owner == request.user


class IsOwnerOrStaff(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user and request.user.is_staff:
            return True
        owner = getattr(obj, "user", None)
        return owner is not None and owner == request.user


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsStaffOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)
