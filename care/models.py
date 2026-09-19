"""产后照护计划协同系统的数据模型。

设计要点：
- 专业意见（ProfessionalOpinion）只追加，新意见可以取代（supersede）旧意见；
- 每次计划变更产生一个 PlanVersion，绑定当时仍然有效的专业意见，并快照项目状态；
- 完成记录（CompletionRecord）与项目事件（ItemEvent）严格只追加，模型层禁止修改与删除；
- 产妇拒绝（Refusal）保留范围与时间，撤销只能由产妇本人发起（权限层强制），记录不删除。
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    MOTHER = "MOTHER", "产妇"
    NURSE = "NURSE", "护理人员"
    NUTRITIONIST = "NUTRITIONIST", "营养师"
    REHAB = "REHAB", "康复师"
    PSYCH = "PSYCH", "心理支持"
    SUPERVISOR = "SUPERVISOR", "服务主管"


class Discipline(models.TextChoices):
    NURSING = "NURSING", "护理"
    NUTRITION = "NUTRITION", "营养"
    REHAB = "REHAB", "康复"
    PSYCH = "PSYCH", "心理支持"


# 专业角色 -> 专业领域；服务主管不属于任何单一领域，但拥有跨领域确认权限。
ROLE_TO_DISCIPLINE = {
    Role.NURSE: Discipline.NURSING,
    Role.NUTRITIONIST: Discipline.NUTRITION,
    Role.REHAB: Discipline.REHAB,
    Role.PSYCH: Discipline.PSYCH,
}

PROFESSIONAL_ROLES = frozenset(ROLE_TO_DISCIPLINE.keys())


class User(AbstractUser):
    """系统用户，角色决定其在协同流程中的权限。"""

    role = models.CharField(max_length=20, choices=Role.choices)

    @property
    def discipline(self):
        return ROLE_TO_DISCIPLINE.get(self.role)

    @property
    def is_professional(self):
        return self.role in PROFESSIONAL_ROLES

    def __str__(self):
        return f"{self.username}({self.get_role_display()})"


class CarePlan(models.Model):
    """某产妇某一天的照护计划。revision 用于乐观锁并发控制。"""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "执行中"
        CONSULTATION = "CONSULTATION", "会商中"
        CLOSED = "CLOSED", "已结案"

    mother = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="care_plans",
        limit_choices_to={"role": Role.MOTHER},
    )
    date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["mother", "date"], name="uniq_plan_per_mother_day"),
        ]

    def __str__(self):
        return f"照护计划 {self.mother.username} {self.date}"


class PlanItem(models.Model):
    """计划中的一个照护项目（当前状态；历史状态见 PlanVersion.items_snapshot）。"""

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "已排定"
        PAUSED = "PAUSED", "已暂停"
        COMPLETED = "COMPLETED", "已完成"
        REFUSED = "REFUSED", "产妇拒绝"

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="items")
    discipline = models.CharField(max_length=20, choices=Discipline.choices)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    optional = models.BooleanField(default=False, help_text="是否为产妇可选择事项")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    pause_reason = models.TextField(blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_discipline_display()}·{self.title}"


class ProfessionalOpinion(models.Model):
    """专业意见：评估 / 建议 / 禁忌 / 执行反馈。内容只追加，不可修改。"""

    class Kind(models.TextChoices):
        ASSESSMENT = "ASSESSMENT", "评估"
        RECOMMENDATION = "RECOMMENDATION", "建议"
        CONTRAINDICATION = "CONTRAINDICATION", "禁忌"
        FEEDBACK = "FEEDBACK", "执行反馈"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "有效"
        SUPERSEDED = "SUPERSEDED", "已被取代"
        WITHDRAWN = "WITHDRAWN", "已撤回"

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="opinions")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="opinions")
    discipline = models.CharField(max_length=20, choices=Discipline.choices)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    content = models.TextField()
    items = models.ManyToManyField(PlanItem, related_name="opinions", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = ProfessionalOpinion.objects.get(pk=self.pk)
            frozen = ("plan_id", "author_id", "discipline", "kind", "content", "supersedes_id")
            for field in frozen:
                if getattr(old, field) != getattr(self, field):
                    raise ValidationError("专业意见内容只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("专业意见不能删除")

    def __str__(self):
        return f"{self.get_discipline_display()}{self.get_kind_display()}#{self.pk}"


class PlanVersion(models.Model):
    """计划版本：每次变更生成，绑定当时有效的专业意见。"""

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    opinions = models.ManyToManyField(ProfessionalOpinion, related_name="bound_versions")
    items_snapshot = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["plan", "number"], name="uniq_version_number"),
        ]
        ordering = ["number"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("计划版本只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("计划版本不能删除")

    def __str__(self):
        return f"计划#{self.plan_id} v{self.number}"


class CompletionRecord(models.Model):
    """完成记录：只追加，任何后来的调整都不能让它消失。"""

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="completions")
    item = models.ForeignKey(PlanItem, on_delete=models.PROTECT, related_name="completions")
    performed_by = models.ForeignKey(User, on_delete=models.PROTECT)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("完成记录只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("完成记录不能删除")


class ItemEvent(models.Model):
    """项目事件（审计日志）：记录每次变更的操作者与依据，只追加。"""

    class Action(models.TextChoices):
        CREATED = "CREATED", "创建项目"
        PAUSED = "PAUSED", "暂停"
        RESUMED = "RESUMED", "恢复"
        COMPLETED = "COMPLETED", "完成"
        REFUSED = "REFUSED", "产妇拒绝"
        REFUSAL_REVOKED = "REFUSAL_REVOKED", "撤销拒绝"

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="events")
    item = models.ForeignKey(PlanItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.TextField(blank=True)
    opinion = models.ForeignKey(
        ProfessionalOpinion, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="triggered_events", help_text="本次变更依据的专业意见",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("事件日志只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("事件日志不能删除")


class Refusal(models.Model):
    """产妇拒绝某项服务：保留拒绝范围与时间，不能被他人代为取消。"""

    class Scope(models.TextChoices):
        THIS_OCCURRENCE = "THIS_OCCURRENCE", "仅本次"
        TODAY = "TODAY", "今日内"
        ITEM = "ITEM", "该项目全部"

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="refusals")
    item = models.ForeignKey(PlanItem, on_delete=models.PROTECT, related_name="refusals")
    mother = models.ForeignKey(User, on_delete=models.PROTECT, related_name="refusals")
    scope = models.CharField(max_length=20, choices=Scope.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="revoked_refusals"
    )

    @property
    def is_active(self):
        return self.revoked_at is None

    def revoke(self, by_user):
        if self.revoked_at is not None:
            raise ValidationError("该拒绝记录已撤销")
        self.revoked_at = timezone.now()
        self.revoked_by = by_user
        self.save(update_fields=["revoked_at", "revoked_by"])

    def delete(self, *args, **kwargs):
        raise ValidationError("拒绝记录不能删除，只能由产妇本人撤销")


class Consultation(models.Model):
    """会商：跨专业意见矛盾时进入，明确尚缺的决定。"""

    class Status(models.TextChoices):
        OPEN = "OPEN", "待会商"
        RESOLVED = "RESOLVED", "已解决"

    plan = models.ForeignKey(CarePlan, on_delete=models.CASCADE, related_name="consultations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    summary = models.TextField(help_text="矛盾点描述")
    opinions = models.ManyToManyField(ProfessionalOpinion, related_name="consultations")
    pending_decisions = models.JSONField(
        default=list,
        help_text='尚缺的决定，如 [{"key": "...", "question": "...", "needed_from": ["NURSING"]}]',
    )
    decisions = models.JSONField(default=list, blank=True, help_text="会商时对每项待决事项给出的结论")
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="resolved_consultations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"会商#{self.pk} 计划#{self.plan_id} {self.get_status_display()}"
