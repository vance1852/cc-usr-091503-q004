from rest_framework.permissions import BasePermission


class _BaseRole(BasePermission):
    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = getattr(getattr(user, "profile", None), "role", None)
        return role in self.allowed_roles


class IsStaff(_BaseRole):
    """护理/营养/康复/心理专业人员以及服务主管。"""

    allowed_roles = (
        "NURSE", "NUTRITIONIST", "REHABILITATOR", "PSYCHOLOGIST", "SUPERVISOR",
    )


class IsProfessional(_BaseRole):
    allowed_roles = ("NURSE", "NUTRITIONIST", "REHABILITATOR", "PSYCHOLOGIST")


class IsSupervisor(_BaseRole):
    allowed_roles = ("SUPERVISOR",)


class IsMother(_BaseRole):
    allowed_roles = ("MOTHER",)


class IsMotherOrStaff(_BaseRole):
    allowed_roles = (
        "MOTHER", "NURSE", "NUTRITIONIST", "REHABILITATOR", "PSYCHOLOGIST",
        "SUPERVISOR",
    )
