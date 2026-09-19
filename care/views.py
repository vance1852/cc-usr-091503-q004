"""API 视图：所有变更类端点都要求 base_revision 做乐观锁校验。"""

import functools

from django.core.exceptions import ValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import (
    CarePlan,
    CompletionRecord,
    Consultation,
    PlanItem,
    Refusal,
    Role,
    User,
)
from .permissions import (
    CanAccessPlan,
    IsMother,
    IsProfessional,
    IsStaff,
    IsStaffOrMother,
    IsSupervisor,
)
from .serializers import (
    CarePlanSerializer,
    CompletionRecordSerializer,
    ConsultationSerializer,
    ItemEventSerializer,
    OpinionSerializer,
    PlanItemSerializer,
    PlanVersionBriefSerializer,
    PlanVersionSerializer,
    RefusalSerializer,
)


def domain_errors(view_func):
    """把业务异常映射为 HTTP 响应：DomainError→400，RevisionConflict→409。"""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except services.DomainError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except services.RevisionConflict as exc:
            return Response(
                {"detail": str(exc), "current_revision": exc.current_revision},
                status=status.HTTP_409_CONFLICT,
            )
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)

    return wrapper


def _base_revision(request):
    value = request.data.get("base_revision", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise services.DomainError("base_revision 必须是整数")


class CarePlanViewSet(viewsets.ModelViewSet):
    serializer_class = CarePlanSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = CarePlan.objects.all().order_by("-date", "id")
        user = self.request.user
        if user is not None and user.is_authenticated and user.role == Role.MOTHER:
            qs = qs.filter(mother=user)
        return qs

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), IsStaff()]
        if self.action in ("add_item", "submit_opinion"):
            return [IsAuthenticated(), IsProfessional()]
        return [IsAuthenticated(), CanAccessPlan()]

    def get_object(self):
        obj = super().get_object()
        self.check_object_permissions(self.request, obj)
        return obj

    @domain_errors
    def create(self, request, *args, **kwargs):
        mother_id = request.data.get("mother")
        date = request.data.get("date")
        if not mother_id or not date:
            raise services.DomainError("必须提供 mother 和 date")
        mother = User.objects.filter(id=mother_id, role=Role.MOTHER).first()
        if mother is None:
            raise services.DomainError("mother 必须是产妇用户")
        plan = services.create_plan(mother, date, request.user)
        return Response(CarePlanSerializer(plan).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @domain_errors
    def add_item(self, request, pk=None):
        plan = self.get_object()
        item = services.add_item(
            plan, request.user, _base_revision(request),
            discipline=request.data.get("discipline", ""),
            title=request.data.get("title", ""),
            detail=request.data.get("detail", ""),
            optional=bool(request.data.get("optional", False)),
        )
        return Response(PlanItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @domain_errors
    def submit_opinion(self, request, pk=None):
        plan = self.get_object()
        opinion = services.submit_opinion(
            plan, request.user, _base_revision(request),
            kind=request.data.get("kind", ""),
            content=request.data.get("content", ""),
            item_ids=request.data.get("item_ids", []),
            supersedes_id=request.data.get("supersedes_id"),
            pause_item_ids=request.data.get("pause_item_ids", None),
        )
        return Response(OpinionSerializer(opinion).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        """版本历史：每个版本绑定当时有效的专业意见。"""
        plan = self.get_object()
        versions = plan.versions.prefetch_related("opinions").all()
        brief = request.query_params.get("brief") == "1"
        cls = PlanVersionBriefSerializer if brief else PlanVersionSerializer
        return Response(cls(versions, many=True).data)

    @action(detail=True, methods=["get"])
    def today(self, request, pk=None):
        """产妇端视图：当天已确认安排、暂停原因、可选择事项。"""
        plan = self.get_object()
        items = plan.items.all()
        data = {
            "date": plan.date,
            "plan_status": plan.status,
            "revision": plan.revision,
            "confirmed": PlanItemSerializer(
                [i for i in items if i.status == PlanItem.Status.SCHEDULED and not i.optional],
                many=True,
            ).data,
            "options": PlanItemSerializer(
                [i for i in items if i.status == PlanItem.Status.SCHEDULED and i.optional],
                many=True,
            ).data,
            "paused": [
                {
                    **PlanItemSerializer(i).data,
                    "pause_reason": i.pause_reason,
                    "paused_at": i.paused_at,
                }
                for i in items
                if i.status == PlanItem.Status.PAUSED
            ],
            "refused": PlanItemSerializer(
                [i for i in items if i.status == PlanItem.Status.REFUSED], many=True
            ).data,
            "completed": PlanItemSerializer(
                [i for i in items if i.status == PlanItem.Status.COMPLETED], many=True
            ).data,
        }
        return Response(data)

    @action(detail=True, methods=["get"])
    def audit(self, request, pk=None):
        """服务主管视图：每次变更依据（事件+版本）与未解决冲突。"""
        plan = self.get_object()
        if request.user.role != Role.SUPERVISOR:
            return Response({"detail": "仅服务主管可查看追溯视图"}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            {
                "plan": CarePlanSerializer(plan).data,
                "versions": PlanVersionBriefSerializer(plan.versions.all(), many=True).data,
                "events": ItemEventSerializer(plan.events.all(), many=True).data,
                "completions": CompletionRecordSerializer(plan.completions.all(), many=True).data,
                "refusals": RefusalSerializer(plan.refusals.all(), many=True).data,
                "open_consultations": ConsultationSerializer(
                    plan.consultations.filter(status=Consultation.Status.OPEN), many=True
                ).data,
            }
        )


class PlanItemViewSet(viewsets.GenericViewSet):
    """项目级操作：暂停 / 恢复 / 完成 / 拒绝。"""

    queryset = PlanItem.objects.select_related("plan", "plan__mother")
    serializer_class = PlanItemSerializer
    http_method_names = ["post", "head", "options"]

    def get_permissions(self):
        if self.action == "refuse":
            return [IsAuthenticated(), IsMother()]
        if self.action == "pause":
            return [IsAuthenticated(), IsStaffOrMother()]
        if self.action in ("resume", "complete"):
            return [IsAuthenticated(), IsStaff()]
        return [IsAuthenticated(), CanAccessPlan()]

    def get_object(self):
        obj = super().get_object()
        self.check_object_permissions(self.request, obj.plan)
        return obj

    @action(detail=True, methods=["post"])
    @domain_errors
    def pause(self, request, pk=None):
        """暂停：任何工作人员可立即暂停；产妇感到不适也可暂停自己计划中的项目。"""
        item = self.get_object()
        plan = item.plan
        if request.user.role == Role.MOTHER and plan.mother_id != request.user.id:
            return Response({"detail": "只能暂停自己计划中的项目"}, status=status.HTTP_403_FORBIDDEN)
        item = services.pause_item(
            plan, item, request.user, _base_revision(request),
            reason=request.data.get("reason", ""),
        )
        return Response(PlanItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    @domain_errors
    def resume(self, request, pk=None):
        """恢复：必须由该项目所属专业的人员或服务主管重新确认。"""
        item = self.get_object()
        item = services.resume_item(
            item.plan, item, request.user, _base_revision(request),
            note=request.data.get("note", ""),
            supersede_opinion_ids=request.data.get("supersede_opinion_ids", []),
        )
        return Response(PlanItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    @domain_errors
    def complete(self, request, pk=None):
        item = self.get_object()
        record = services.complete_item(
            item.plan, item, request.user, _base_revision(request),
            note=request.data.get("note", ""),
        )
        return Response(CompletionRecordSerializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @domain_errors
    def refuse(self, request, pk=None):
        """产妇拒绝：记录拒绝范围与时间。"""
        item = self.get_object()
        refusal = services.refuse_item(
            item.plan, item, request.user, _base_revision(request),
            scope=request.data.get("scope", ""),
            reason=request.data.get("reason", ""),
        )
        return Response(RefusalSerializer(refusal).data, status=status.HTTP_201_CREATED)


class RefusalViewSet(viewsets.GenericViewSet):
    queryset = Refusal.objects.select_related("plan", "item", "mother")
    serializer_class = RefusalSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == Role.MOTHER:
            qs = qs.filter(mother=self.request.user)
        return qs

    @action(detail=True, methods=["post"])
    @domain_errors
    def revoke(self, request, pk=None):
        """撤销拒绝：只能由产妇本人发起，其他人员（含主管）不能代为取消。"""
        refusal = self.get_object()
        if refusal.mother_id != request.user.id:
            return Response(
                {"detail": "拒绝只能由产妇本人撤销，其他人员不能代为取消"},
                status=status.HTTP_403_FORBIDDEN,
            )
        refusal = services.revoke_refusal(refusal, request.user, _base_revision(request))
        return Response(RefusalSerializer(refusal).data)


class ConsultationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConsultationSerializer

    def get_queryset(self):
        qs = Consultation.objects.prefetch_related("opinions").order_by("-id")
        if self.request.user.role == Role.MOTHER:
            qs = qs.filter(plan__mother=self.request.user)
        return qs

    @action(detail=True, methods=["post"], permission_classes=[IsSupervisor])
    @domain_errors
    def resolve(self, request, pk=None):
        """解决会商：服务主管对每一项尚缺决定给出结论。"""
        consultation = self.get_object()
        consultation = services.resolve_consultation(
            consultation, request.user, _base_revision(request),
            decisions=request.data.get("decisions", []),
            resolution=request.data.get("resolution", ""),
        )
        return Response(ConsultationSerializer(consultation).data)


class CompletionRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """完成记录只读：不提供修改与删除端点，保证只追加。"""

    serializer_class = CompletionRecordSerializer

    def get_queryset(self):
        qs = CompletionRecord.objects.all().order_by("id")
        if self.request.user.role == Role.MOTHER:
            qs = qs.filter(plan__mother=self.request.user)
        return qs
