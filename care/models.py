from django.conf import settings
from django.db import models


class Role(models.TextChoices):
    NURSE = "NURSE", "护理"
    NUTRITIONIST = "NUTRITIONIST", "营养"
    REHABILITATOR = "REHABILITATOR", "康复"
    PSYCHOLOGIST = "PSYCHOLOGIST", "心理支持"
    SUPERVISOR = "SUPERVISOR", "服务主管"
    MOTHER = "MOTHER", "产妇"


PROFESSIONAL_ROLES = {
    Role.NURSE,
    Role.NUTRITIONIST,
    Role.REHABILITATOR,
    Role.PSYCHOLOGIST,
}

# 角色 -> 专业条线（专业意见与项目归属都用这套条线）
ROLE_DISCIPLINE = {
    Role.NURSE: "care",
    Role.NUTRITIONIST: "nutrition",
    Role.REHABILITATOR: "rehab",
    Role.PSYCHOLOGIST: "psych",
}

DISCIPLINE_CHOICES = (
    ("care", "护理"),
    ("nutrition", "营养"),
    ("rehab", "康复"),
    ("psych", "心理支持"),
)
DISCIPLINE_LABELS = dict(DISCIPLINE_CHOICES)


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    display_name = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "人员档案"

    def __str__(self):
        return f"{self.display_name or self.user.username}({self.get_role_display()})"

    @property
    def discipline(self):
        """专业条线；主管与产妇没有条线。"""
        return ROLE_DISCIPLINE.get(self.role, "")

    @property
    def is_professional(self):
        return self.role in PROFESSIONAL_ROLES

    @property
    def is_supervisor(self):
        return self.role == Role.SUPERVISOR

    @property
    def is_mother(self):
        return self.role == Role.MOTHER


class Plan(models.Model):
    """某位产妇某一天的照护计划。"""

    mother = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="care_plans"
    )
    care_date = models.DateField("照护日期", db_index=True)
    title = models.CharField(max_length=128)
    # 乐观锁版本号：任何安排变更后 +1，发布版本必须携带期望值
    revision = models.PositiveIntegerField(default=1)
    current_version = models.ForeignKey(
        "PlanVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "照护计划"
        unique_together = ("mother", "care_date")
        ordering = ["-care_date"]

    def __str__(self):
        return f"{self.title} {self.care_date} (rev {self.revision})"


class ItemState(models.TextChoices):
    PROPOSED = "PROPOSED", "待确认"
    CONFIRMED = "CONFIRMED", "已确认"
    PAUSED = "PAUSED", "已暂停"
    IN_CONFLICT = "IN_CONFLICT", "会商中"
    REFUSED = "REFUSED", "已拒绝"


class PlanItem(models.Model):
    """计划中的单个服务项目（换药、康复训练、餐食、心理疏导等）。"""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="items")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=128)
    discipline = models.CharField(max_length=16, choices=DISCIPLINE_CHOICES)
    scheduled_time = models.TimeField(null=True, blank=True)
    optional = models.BooleanField(default=False, help_text="可选项：产妇可自行选择是否接受")
    state = models.CharField(
        max_length=16, choices=ItemState.choices, default=ItemState.PROPOSED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "计划项目"
        unique_together = ("plan", "code")
        ordering = ["scheduled_time", "code"]

    def __str__(self):
        return f"{self.code} {self.name} [{self.state}]"


class OpinionType(models.TextChoices):
    ASSESSMENT = "ASSESSMENT", "评估"
    RECOMMENDATION = "RECOMMENDATION", "建议"
    CONTRAINDICATION = "CONTRAINDICATION", "禁忌"
    FEEDBACK = "FEEDBACK", "执行反馈"
    CONFIRMATION = "CONFIRMATION", "恢复确认"


class ProfessionalOpinion(models.Model):
    """
    专业意见（评估/建议/禁忌/执行反馈/恢复确认）。
    只追加、不修改不删除；每个计划版本绑定发布时点已存在的全部意见。
    """

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="opinions")
    item = models.ForeignKey(
        PlanItem, null=True, blank=True, on_delete=models.CASCADE, related_name="opinions"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opinions"
    )
    discipline = models.CharField(max_length=16)
    opinion_type = models.CharField(max_length=20, choices=OpinionType.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "专业意见"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_opinion_type_display()}@{self.discipline}: {self.content[:20]}"


class PauseEvent(models.Model):
    """项目暂停记录；恢复时写入 cleared_* 字段，原始记录保留。"""

    item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name="pause_events")
    paused_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="pauses")
    reason_opinion = models.ForeignKey(
        ProfessionalOpinion, null=True, blank=True, on_delete=models.PROTECT,
        related_name="pauses",
    )
    reason_text = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="cleared_pauses",
    )
    resume_event = models.ForeignKey(
        "ResumeEvent", null=True, blank=True, on_delete=models.SET_NULL, related_name="cleared"
    )

    class Meta:
        ordering = ["created_at"]


class ResumeEvent(models.Model):
    """恢复执行记录：必须由有权限的专业人员（项目所属条线或主管）重新确认。"""

    item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name="resume_events")
    resumed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="resumes")
    confirmation = models.ForeignKey(
        ProfessionalOpinion, on_delete=models.PROTECT, related_name="resumes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Refusal(models.Model):
    """
    产妇拒绝记录。只追加；拒绝范围与时间永久保留，
    任何工作人员都不能代为取消或改动。
    """

    item = models.OneToOneField(PlanItem, on_delete=models.PROTECT, related_name="refusal")
    mother = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name="refusals")
    scope = models.CharField(max_length=255, blank=True, help_text="拒绝范围，如当日、该项目全程")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class CompletionRecord(models.Model):
    """完成记录：只追加，计划后续调整不影响既有记录。"""

    item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name="completions")
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name="completions")
    comment = models.CharField(max_length=255, blank=True)
    plan_version = models.ForeignKey(
        "PlanVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="completions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class ConsultationIssue(models.Model):
    """跨专业意见矛盾的会商议题，明确记录尚缺的决定。"""

    class Status(models.TextChoices):
        OPEN = "OPEN", "待会商"
        RESOLVED = "RESOLVED", "已决定"

    class Decision(models.TextChoices):
        PROCEED = "PROCEED", "会商后恢复执行"
        HALT = "HALT", "会商后维持暂停"

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="issues")
    item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name="issues")
    opinions = models.ManyToManyField(ProfessionalOpinion, related_name="issues")
    missing_decision = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    decision = models.CharField(max_length=12, choices=Decision.choices, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="resolved_issues",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class PlanVersion(models.Model):
    """计划版本：发布时点的项目快照与有效专业意见绑定，均不可变。"""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(help_text="发布时计划的修订号")
    note = models.CharField(max_length=255, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="published_versions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("plan", "number")
        ordering = ["-number"]


class VersionItem(models.Model):
    version = models.ForeignKey(PlanVersion, on_delete=models.CASCADE, related_name="items")
    source = models.ForeignKey(
        PlanItem, null=True, on_delete=models.SET_NULL, related_name="version_snapshots"
    )
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=128)
    discipline = models.CharField(max_length=16)
    scheduled_time = models.TimeField(null=True, blank=True)
    optional = models.BooleanField(default=False)
    state = models.CharField(max_length=16, choices=ItemState.choices)

    class Meta:
        ordering = ["scheduled_time", "code"]


class VersionOpinion(models.Model):
    """版本与当时有效专业意见的绑定，同时冻结内容防止历史被改写。"""

    version = models.ForeignKey(PlanVersion, on_delete=models.CASCADE,
                                related_name="opinion_bindings")
    source = models.ForeignKey(
        ProfessionalOpinion, null=True, on_delete=models.SET_NULL,
        related_name="version_bindings",
    )
    author_name = models.CharField(max_length=128)
    discipline = models.CharField(max_length=16)
    opinion_type = models.CharField(max_length=20, choices=OpinionType.choices)
    content = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["created_at"]


class ChangeAction(models.TextChoices):
    CREATE_PLAN = "CREATE_PLAN", "建立计划"
    ADD_ITEM = "ADD_ITEM", "新增项目"
    OPINION = "OPINION", "提交专业意见"
    PAUSE = "PAUSE", "暂停项目"
    RESUME = "RESUME", "恢复项目"
    REFUSE = "REFUSE", "产妇拒绝"
    SELECT = "SELECT", "产妇选择"
    COMPLETE = "COMPLETE", "完成记录"
    PUBLISH = "PUBLISH", "发布版本"
    CONFLICT_OPEN = "CONFLICT_OPEN", "进入会商"
    RESOLVE = "RESOLVE", "会商决定"


class ChangeLog(models.Model):
    """变更审计：服务主管据此追溯每次变更的依据。"""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="changes")
    item = models.ForeignKey(PlanItem, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="changes")
    action = models.CharField(max_length=16, choices=ChangeAction.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="changes")
    opinion = models.ForeignKey(
        ProfessionalOpinion, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="changes",
    )
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
