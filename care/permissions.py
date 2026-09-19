"""角色权限：产妇 / 护理 / 营养 / 康复 / 心理支持 / 服务主管。"""

from rest_framework.permissions import BasePermission

from .models import PROFESSIONAL_ROLES, Role


class IsMother(BasePermission):
    message = "仅产妇本人可操作"

    def has_permission(self, request, view):
        return request.user.role == Role.MOTHER


class IsProfessional(BasePermission):
    """护理、营养、康复、心理支持四类专业人员。"""

    message = "仅专业人员（护理/营养/康复/心理支持）可操作"

    def has_permission(self, request, view):
        return request.user.role in PROFESSIONAL_ROLES


class IsSupervisor(BasePermission):
    message = "仅服务主管可操作"

    def has_permission(self, request, view):
        return request.user.role == Role.SUPERVISOR


class IsStaff(BasePermission):
    """专业人员或服务主管。"""

    message = "仅工作人员可操作"

    def has_permission(self, request, view):
        return request.user.role in PROFESSIONAL_ROLES or request.user.role == Role.SUPERVISOR


class IsStaffOrMother(BasePermission):
    """工作人员或产妇本人（用于"立即暂停"这类安全动作）。"""

    message = "仅工作人员或产妇本人可操作"

    def has_permission(self, request, view):
        return request.user.role in PROFESSIONAL_ROLES or request.user.role in (
            Role.SUPERVISOR,
            Role.MOTHER,
        )


class CanAccessPlan(BasePermission):
    """产妇只能访问自己的计划；工作人员可访问所服务的计划。"""

    message = "无权访问该计划"

    def has_object_permission(self, request, view, obj):
        plan = obj if hasattr(obj, "mother") else obj.plan
        if request.user.role == Role.MOTHER:
            return plan.mother_id == request.user.id
        return True
