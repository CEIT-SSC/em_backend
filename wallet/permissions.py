from rest_framework.permissions import BasePermission, IsAuthenticated


class IsStaffUser(BasePermission):
    message = "Staff privileges are required for this wallet operation."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


IsAuthenticatedUser = IsAuthenticated
