from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import (
    ConsultationIssue,
    ItemState,
    Plan,
    PlanItem,
    PlanVersion,
    Role,
)
from .permissions import (
    IsMother,
    IsMotherOrStaff,
    IsProfessional,
    IsStaff,
    IsSupervisor,
)
from .serializers import (
    ChangeLogSerializer,
    CompleteSerializer,
    IssueSerializer,
    ItemCreateSerializer,
    ItemSerializer,
    OpinionCreateSerializer,
    OpinionSerializer,
    PauseSerializer,
    PlanCreateSerializer,
    PlanSerializer,
    PublishSerializer,
    RefusalCreateSerializer,
    ResumeSerializer,
    ResolveSerializer,
    RevisionSerializer,
    RoleAssignSerializer,
    VersionSerializer,
)

User = get_user_model()


class _RevisionConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "计划已被他人修改，请刷新后重试"
    default_code = "revision_conflict"


class _BusinessConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "当前状态不允许该操作"
    default_code = "business_conflict"


def _translate(exc):
    if isinstance(exc, services.RevisionConflict):
        return _RevisionConflict(str(exc))
    if isinstance(exc, services.BusinessConflict):
        return _BusinessConflict(str(exc))
    raise exc


class PlanViewSet(viewsets.ModelViewSet):
    queryset = Plan.objects.select_related("mother", "current_version")
    serializer_class = PlanSerializer
    permission_classes = [IsMotherOrStaff]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.profile.is_mother:
            qs = qs.filter(mother=self.request.user)
        return qs

    def create(self, request, *args, **kwargs):
        if not (request.user.profile.is_professional
                or request.user.profile.is_supervisor):
            return Response({"detail": "仅工作人员可以建立计划"}, status=403)
        serializer = PlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mother = get_object_or_404(
            User, username=serializer.validated_data["mother_username"]
        )
        if getattr(getattr(mother, "profile", None), "role", None) != Role.MOTHER:
            return Response({"detail": "被指定人不是产妇账号"}, status=400)
        try:
            plan = services.create_plan(
                actor=request.user, mother=mother,
                care_date=serializer.validated_data["care_date"],
                title=serializer.validated_data["title"],
            )
        except Exception as exc:  # 统一冲突翻译（如唯一约束竞争）
            raise _translate(exc)
        return Response(PlanSerializer(plan).data, status=201)

    # ---- 计划内聚合资源 -------------------------------------------------

    @action(detail=True, methods=["get", "post"])
    def items(self, request, pk=None):
        plan = self.get_object()
        if request.method == "GET":
            return Response(ItemSerializer(plan.items.all(), many=True).data)
        if not (request.user.profile.is_professional
                or request.user.profile.is_supervisor):
            return Response({"detail": "仅工作人员可以新增项目"}, status=403)
        serializer = ItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = services.add_item(
                actor=request.user, plan=plan, code=data["code"], name=data["name"],
                discipline=data["discipline"],
                scheduled_time=data.get("scheduled_time"),
                optional=data.get("optional", False),
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(ItemSerializer(item).data, status=201)

    @action(detail=True, methods=["get", "post"])
    def opinions(self, request, pk=None):
        plan = self.get_object()
        if request.method == "GET":
            return Response(
                OpinionSerializer(plan.opinions.all(), many=True).data
            )
        if not request.user.profile.is_professional:
            return Response({"detail": "仅专业人员可以提交意见"}, status=403)
        serializer = OpinionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item = data.get("item")
        if item is not None and item.plan_id != plan.id:
            return Response({"detail": "项目不属于该计划"}, status=400)
        try:
            opinion = services.submit_opinion(
                actor=request.user, plan=plan,
                opinion_type=data["opinion_type"], content=data["content"],
                item=item, expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(OpinionSerializer(opinion).data, status=201)

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        plan = self.get_object()
        qs = plan.versions.prefetch_related("items", "opinion_bindings")
        return Response(VersionSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"],
            url_path=r"versions/(?P<version_pk>[0-9]+)")
    def version_detail(self, request, pk=None, version_pk=None):
        plan = self.get_object()
        version = get_object_or_404(
            PlanVersion.objects.prefetch_related("items", "opinion_bindings"),
            pk=version_pk, plan=plan,
        )
        return Response(VersionSerializer(version).data)

    @action(detail=True, methods=["get"])
    def changes(self, request, pk=None):
        plan = self.get_object()
        return Response(
            ChangeLogSerializer(plan.changes.all(), many=True).data
        )

    @action(detail=True, methods=["get"])
    def issues(self, request, pk=None):
        plan = self.get_object()
        return Response(
            IssueSerializer(plan.issues.all(), many=True).data
        )

    # ---- 发布版本 -------------------------------------------------------

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        plan = self.get_object()
        if not (request.user.profile.is_professional
                or request.user.profile.is_supervisor):
            return Response({"detail": "仅工作人员可以发布版本"}, status=403)
        serializer = PublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            version = services.publish_version(
                actor=request.user, plan=plan, note=data.get("note", ""),
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(VersionSerializer(version).data, status=201)

    # ---- 项目级动作 -----------------------------------------------------

    def _item(self, plan, item_pk):
        return get_object_or_404(PlanItem, pk=item_pk, plan=plan)

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/pause")
    def pause_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = PauseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = services.pause_item(
                actor=request.user, item=self._item(plan, item_pk),
                reason=data["reason"],
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(ItemSerializer(item).data)

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/resume")
    def resume_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = ResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = services.resume_item(
                actor=request.user, item=self._item(plan, item_pk),
                comment=data["comment"],
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(ItemSerializer(item).data)

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/confirm")
    def confirm_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = RevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = services.confirm_item(
                actor=request.user, item=self._item(plan, item_pk),
                expected_revision=serializer.validated_data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(ItemSerializer(item).data)

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/complete")
    def complete_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            record = services.complete_item(
                actor=request.user, item=self._item(plan, item_pk),
                comment=data.get("comment", ""),
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(
            {"id": record.id, "comment": record.comment,
             "completed_by": record.completed_by.username,
             "created_at": record.created_at},
            status=201,
        )

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/refuse")
    def refuse_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = RefusalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            refusal = services.refuse_item(
                mother=request.user, item=self._item(plan, item_pk),
                scope=data.get("scope", ""), reason=data.get("reason", ""),
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(
            {"id": refusal.id, "scope": refusal.scope, "reason": refusal.reason,
             "created_at": refusal.created_at},
            status=201,
        )

    @action(detail=True, methods=["post"],
            url_path=r"items/(?P<item_pk>[0-9]+)/select")
    def select_item(self, request, pk=None, item_pk=None):
        plan = self.get_object()
        serializer = RevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = services.select_item(
                mother=request.user, item=self._item(plan, item_pk),
                expected_revision=serializer.validated_data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(ItemSerializer(item).data)


class IssueResolveView(APIView):
    permission_classes = [IsSupervisor]

    def post(self, request, issue_id):
        issue = get_object_or_404(ConsultationIssue, pk=issue_id)
        serializer = ResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            issue = services.resolve_issue(
                actor=request.user, issue=issue,
                decision=data["decision"], note=data["note"],
                expected_revision=data["expected_revision"],
            )
        except Exception as exc:
            raise _translate(exc)
        return Response(IssueSerializer(issue).data)


class OpenIssuesView(APIView):
    """服务主管视角：全部未解决的跨专业冲突。"""

    permission_classes = [IsSupervisor]

    def get(self, request):
        qs = (
            ConsultationIssue.objects.filter(status=ConsultationIssue.Status.OPEN)
            .select_related("plan", "item")
        )
        return Response(IssueSerializer(qs, many=True).data)


class MotherTodayView(APIView):
    """产妇端：当天已确认安排、暂停原因、可选择事项、本人拒绝记录。"""

    permission_classes = [IsMother]

    def get(self, request):
        from django.utils import timezone

        today = timezone.localdate()
        plan = (
            Plan.objects.filter(mother=request.user, care_date=today)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=PlanItem.objects.prefetch_related(
                        "refusal", "pause_events"
                    ),
                )
            )
            .first()
        )
        if plan is None:
            return Response({"date": today, "plan": None, "confirmed": [],
                             "paused": [], "options": [], "refused": []})

        confirmed, paused, options, refused = [], [], [], []
        for item in plan.items.all():
            payload = ItemSerializer(item).data
            if item.state == ItemState.CONFIRMED:
                confirmed.append(payload)
                if item.optional:
                    options.append(payload)
            elif item.state in (ItemState.PAUSED, ItemState.IN_CONFLICT):
                paused.append(payload)
            elif item.state == ItemState.REFUSED:
                refused.append(payload)
        return Response({
            "date": today,
            "plan": PlanSerializer(plan).data,
            "revision": plan.revision,
            "confirmed": confirmed,
            "paused": paused,
            "options": options,
            "refused": refused,
        })


class RoleAssignView(APIView):
    """
    分配/创建账号角色（演示环境用；生产应由用户管理模块接管）。
    已登录的任意用户首次可通过命令行 seed，接口仅服务主管可用。
    """

    permission_classes = [IsSupervisor]

    def post(self, request):
        serializer = RoleAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from .models import Profile

        user, created = User.objects.get_or_create(
            username=data["username"],
            defaults={"is_staff": False},
        )
        if not created:
            user.profile.role = data["role"]
            user.profile.save(update_fields=["role"])
        else:
            user.set_password(User.objects.make_random_password())
            user.save()
            Profile.objects.create(
                user=user, role=data["role"],
                display_name=data.get("display_name", ""),
            )
        return Response(
            {"username": user.username, "role": user.profile.role,
             "created": created},
            status=201 if created else 200,
        )
