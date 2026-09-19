"""
照护计划领域服务。

所有写操作都在这里集中处理：
- revision 乐观锁（条件 UPDATE），防止跨专业并发覆盖；
- 暂停即时生效，恢复必须由项目所属条线的专业人员重新确认；
- 拒绝仅产妇本人可提交，工作人员无任何途径取消；
- 完成记录只追加；
- 跨专业矛盾自动进入会商并登记"尚缺的决定"；
- 发布版本时把当时全部有效专业意见冻结绑定到版本。
"""
from __future__ import annotations

import functools

from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    DISCIPLINE_LABELS,
    ChangeAction,
    ChangeLog,
    CompletionRecord,
    ConsultationIssue,
    ItemState,
    OpinionType,
    PauseEvent,
    Plan,
    PlanItem,
    PlanVersion,
    ProfessionalOpinion,
    Refusal,
    ResumeEvent,
    VersionItem,
    VersionOpinion,
)


class RevisionConflict(Exception):
    """客户端携带的 expected_revision 已过期，或并发写冲突。"""


class BusinessConflict(Exception):
    """当前状态不允许该操作（如会商未决时恢复、已拒绝项目再安排）。"""


def guarded_atomic(func):
    """
    事务装饰器：把 SQLite 的 BUSY/LOCKED 统一翻译为版本冲突。
    高并发下 BEGIN IMMEDIATE 抢锁或事务内语句可能等不到写锁，
    此时唯一安全的响应就是让调用方重新获取计划后重试。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except OperationalError as exc:
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                raise RevisionConflict("并发修改冲突（数据库写锁等待超时），请刷新后重试")
            raise

    return wrapper


def _profile(user):
    return user.profile


def _log(plan, action, actor, *, item=None, opinion=None, **detail):
    return ChangeLog.objects.create(
        plan=plan, action=action, actor=actor, item=item, opinion=opinion, detail=detail
    )


def _bump_revision(plan, expected_revision):
    """
    以条件 UPDATE 抢占修订号，作为整个事务的第一道写操作。
    SQLite（含 WAL）下同一时刻只有一个写事务：
    - 若已有他人提交新版本，revision 谓词失配 -> 更新 0 行；
    - 若快照过旧，SQLite 直接抛 database is locked/busy。
    两种情况都回滚并返回 409，调用方应重新获取计划后重试。
    """
    if expected_revision is None:
        raise PermissionDenied("必须携带 expected_revision")
    if plan.revision != expected_revision:
        raise RevisionConflict(
            f"计划已被他人修改（当前 revision={plan.revision}，提交基于 {expected_revision}）"
        )
    try:
        updated = (
            Plan.objects.filter(pk=plan.pk, revision=expected_revision)
            .update(revision=F("revision") + 1)
        )
    except OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise RevisionConflict("并发修改冲突，请重新获取计划后再提交")
        raise
    if updated == 0:
        raise RevisionConflict(
            f"计划已被他人修改（提交基于 revision={expected_revision}）"
        )
    plan.revision = expected_revision + 1


def _require_staff(profile):
    if not (profile.is_professional or profile.is_supervisor):
        raise PermissionDenied("仅工作人员可执行该操作")


def _require_professional(profile):
    if not profile.is_professional:
        raise PermissionDenied("仅护理/营养/康复/心理专业人员可执行该操作")


@guarded_atomic
def create_plan(*, actor, mother, care_date, title):
    profile = _profile(actor)
    _require_staff(profile)
    plan = Plan.objects.create(mother=mother, care_date=care_date, title=title)
    _log(plan, ChangeAction.CREATE_PLAN, actor, mother=mother.username,
         care_date=str(care_date))
    return plan


@guarded_atomic
def add_item(*, actor, plan, code, name, discipline, scheduled_time=None,
             optional=False, expected_revision=None):
    profile = _profile(actor)
    _require_staff(profile)
    plan = Plan.objects.get(pk=plan.pk)
    _bump_revision(plan, expected_revision)
    item = PlanItem.objects.create(
        plan=plan, code=code, name=name, discipline=discipline,
        scheduled_time=scheduled_time, optional=optional,
    )
    _log(plan, ChangeAction.ADD_ITEM, actor, item=item,
         code=code, discipline=discipline, optional=optional)
    return item


@guarded_atomic
def submit_opinion(*, actor, plan, opinion_type, content, item=None,
                   expected_revision=None):
    """
    提交评估/建议/禁忌/执行反馈。意见只追加。

    禁忌（CONTRAINDICATION）会立即影响相关项目：
    - 与项目同条线：项目立即暂停；
    - 跨条线：项目暂停并自动进入会商，登记尚缺决定。
    """
    profile = _profile(actor)
    _require_professional(profile)
    plan = Plan.objects.get(pk=plan.pk)
    if item is not None:
        item = PlanItem.objects.get(pk=item.pk)
        if item.plan_id != plan.id:
            raise BusinessConflict("项目不属于该计划")
    _bump_revision(plan, expected_revision)

    opinion = ProfessionalOpinion.objects.create(
        plan=plan, item=item, author=actor, discipline=profile.discipline,
        opinion_type=opinion_type, content=content,
    )
    _log(plan, ChangeAction.OPINION, actor, item=item, opinion=opinion,
         opinion_type=opinion_type, discipline=profile.discipline)

    if item is not None and opinion_type == OpinionType.CONTRAINDICATION:
        _apply_contraindication(plan=plan, item=item, opinion=opinion, actor=actor)
    return opinion


def _apply_contraindication(*, plan, item, opinion, actor):
    if item.state == ItemState.REFUSED:
        # 产妇已拒绝的项目不再被专业操作改变状态，禁忌仅作为意见留存
        return
    pause = PauseEvent.objects.create(
        item=item, paused_by=actor, reason_opinion=opinion,
        reason_text=opinion.content[:255],
    )
    if opinion.discipline == item.discipline:
        item.state = ItemState.PAUSED
        item.save(update_fields=["state"])
        _log(plan, ChangeAction.PAUSE, actor, item=item, opinion=opinion,
             pause_event_id=pause.id, reason=opinion.content[:255])
    else:
        item.state = ItemState.IN_CONFLICT
        item.save(update_fields=["state"])
        missing = (
            f"尚缺决定：{DISCIPLINE_LABELS.get(opinion.discipline, opinion.discipline)}"
            f"条线的禁忌与{DISCIPLINE_LABELS.get(item.discipline, item.discipline)}"
            f"条线安排冲突，需会商后由"
            f"{DISCIPLINE_LABELS.get(item.discipline, item.discipline)}条线确认是否恢复"
        )
        issue = ConsultationIssue.objects.create(
            plan=plan, item=item, missing_decision=missing
        )
        issue.opinions.add(opinion)
        _log(plan, ChangeAction.CONFLICT_OPEN, actor, item=item, opinion=opinion,
             issue_id=issue.id, pause_event_id=pause.id, missing_decision=missing)


@guarded_atomic
def pause_item(*, actor, item, reason, expected_revision=None):
    """出现新的不适等情况时，任何专业人员都可立即暂停相关项目。"""
    profile = _profile(actor)
    _require_professional(profile)
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    _bump_revision(plan, expected_revision)
    # 以下状态校验放在抢占修订号之后：过期提交必须得到 RevisionConflict，
    # 而不是恰好命中别人刚写入的状态而报业务冲突（抢占会随事务回滚）
    if item.state == ItemState.REFUSED:
        raise BusinessConflict("产妇已拒绝的项目不能再被工作人员暂停或改期")
    if item.state in (ItemState.PAUSED, ItemState.IN_CONFLICT):
        raise BusinessConflict("项目已处于暂停/会商状态")

    opinion = ProfessionalOpinion.objects.create(
        plan=plan, item=item, author=actor, discipline=profile.discipline,
        opinion_type=OpinionType.ASSESSMENT, content=reason,
    )
    pause = PauseEvent.objects.create(
        item=item, paused_by=actor, reason_opinion=opinion, reason_text=reason[:255]
    )
    if profile.discipline == item.discipline:
        item.state = ItemState.PAUSED
        item.save(update_fields=["state"])
        _log(plan, ChangeAction.PAUSE, actor, item=item, opinion=opinion,
             pause_event_id=pause.id, reason=reason[:255])
    else:
        item.state = ItemState.IN_CONFLICT
        item.save(update_fields=["state"])
        missing = (
            f"尚缺决定：{DISCIPLINE_LABELS.get(profile.discipline, profile.discipline)}"
            f"发现“{reason[:50]}”，与"
            f"{DISCIPLINE_LABELS.get(item.discipline, item.discipline)}"
            f"条线安排存在跨专业矛盾，需会商决定"
        )
        issue = ConsultationIssue.objects.create(
            plan=plan, item=item, missing_decision=missing
        )
        issue.opinions.add(opinion)
        _log(plan, ChangeAction.CONFLICT_OPEN, actor, item=item, opinion=opinion,
             issue_id=issue.id, pause_event_id=pause.id, missing_decision=missing)
    return item


@guarded_atomic
def confirm_item(*, actor, item, expected_revision=None):
    """确认安排（待确认 -> 已确认），仅项目所属条线专业人员。"""
    profile = _profile(actor)
    _require_professional(profile)
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    if profile.discipline != item.discipline:
        raise PermissionDenied("只有该项目所属条线的专业人员可以确认安排")
    _bump_revision(plan, expected_revision)
    if item.state != ItemState.PROPOSED:
        raise BusinessConflict(f"项目当前状态为 {item.state}，不能确认")

    opinion = ProfessionalOpinion.objects.create(
        plan=plan, item=item, author=actor, discipline=profile.discipline,
        opinion_type=OpinionType.RECOMMENDATION, content="专业人员确认安排",
    )
    item.state = ItemState.CONFIRMED
    item.save(update_fields=["state"])
    _log(plan, ChangeAction.OPINION, actor, item=item, opinion=opinion,
         new_state=ItemState.CONFIRMED)
    return item


@guarded_atomic
def resume_item(*, actor, item, comment, expected_revision=None):
    """
    恢复执行：必须由项目所属条线的有权限专业人员提交恢复确认；
    会商未决时一律拒绝恢复；其他条线专业人员无权恢复。
    """
    profile = _profile(actor)
    _require_professional(profile)
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    if profile.discipline != item.discipline:
        raise PermissionDenied("恢复执行必须由项目所属条线的专业人员重新确认")
    _bump_revision(plan, expected_revision)
    # 状态校验放在抢占之后，过期提交一律得到 RevisionConflict（异常会回滚抢占）
    if item.state not in (ItemState.PAUSED, ItemState.IN_CONFLICT):
        raise BusinessConflict(f"项目当前状态为 {item.state}，无需恢复")
    open_issue = item.issues.filter(status=ConsultationIssue.Status.OPEN).exists()
    if open_issue:
        raise BusinessConflict("跨专业矛盾尚未会商决定，不能恢复执行")
    latest_pause = item.pause_events.order_by("-created_at", "-id").first()
    if latest_pause is None or latest_pause.cleared_at is not None:
        raise BusinessConflict("没有待恢复的暂停记录")

    confirmation = ProfessionalOpinion.objects.create(
        plan=plan, item=item, author=actor, discipline=profile.discipline,
        opinion_type=OpinionType.CONFIRMATION, content=comment,
    )
    resume = ResumeEvent.objects.create(
        item=item, resumed_by=actor, confirmation=confirmation
    )
    latest_pause.cleared_at = timezone.now()
    latest_pause.cleared_by = actor
    latest_pause.resume_event = resume
    latest_pause.save(update_fields=["cleared_at", "cleared_by", "resume_event"])
    item.state = ItemState.CONFIRMED
    item.save(update_fields=["state"])
    _log(plan, ChangeAction.RESUME, actor, item=item, opinion=confirmation,
         resume_event_id=resume.id, pause_event_id=latest_pause.id, comment=comment)
    return item


@guarded_atomic
def refuse_item(*, mother, item, scope="", reason="", expected_revision=None):
    """产妇本人拒绝服务：记录拒绝范围与时间，工作人员无法取消。"""
    profile = _profile(mother)
    if not profile.is_mother:
        raise PermissionDenied("只有产妇本人可以拒绝服务")
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    if plan.mother_id != mother.id:
        raise PermissionDenied("不能拒绝他人计划中的项目")
    _bump_revision(plan, expected_revision)
    if Refusal.objects.filter(item=item).exists():
        raise BusinessConflict("该项目已存在拒绝记录")

    refusal = Refusal.objects.create(
        item=item, mother=mother, scope=scope, reason=reason
    )
    item.state = ItemState.REFUSED
    item.save(update_fields=["state"])
    _log(plan, ChangeAction.REFUSE, mother, item=item,
         scope=scope, reason=reason[:255], refused_at=timezone.now().isoformat())
    return refusal


@guarded_atomic
def select_item(*, mother, item, expected_revision=None):
    """产妇选择可选项（仅记录选择，不改变项目确认状态）。"""
    profile = _profile(mother)
    if not profile.is_mother:
        raise PermissionDenied("只有产妇本人可以选择服务项目")
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    if plan.mother_id != mother.id:
        raise PermissionDenied("不能选择他人计划中的项目")
    _bump_revision(plan, expected_revision)
    if not item.optional:
        raise BusinessConflict("该项目不是可选项")
    if item.state != ItemState.CONFIRMED:
        raise BusinessConflict(f"项目当前状态为 {item.state}，暂不可选")

    _log(plan, ChangeAction.SELECT, mother, item=item,
         selected_at=timezone.now().isoformat())
    return item


@guarded_atomic
def complete_item(*, actor, item, comment="", expected_revision=None):
    """登记完成记录：只追加；仅已确认项目可登记，暂停/会商/拒绝均不可。"""
    profile = _profile(actor)
    _require_professional(profile)
    item = PlanItem.objects.get(pk=item.pk)
    plan = Plan.objects.get(pk=item.plan_id)
    _bump_revision(plan, expected_revision)
    if item.state != ItemState.CONFIRMED:
        raise BusinessConflict(f"项目当前状态为 {item.state}，只有已确认项目可登记完成")

    record = CompletionRecord.objects.create(
        item=item, completed_by=actor, comment=comment,
        plan_version=plan.current_version,
    )
    _log(plan, ChangeAction.COMPLETE, actor, item=item,
         completion_id=record.id, comment=comment[:255])
    return record


@guarded_atomic
def resolve_issue(*, actor, issue, decision, note, expected_revision=None):
    """服务主管对跨专业矛盾作出会商决定。"""
    profile = _profile(actor)
    if not profile.is_supervisor:
        raise PermissionDenied("只有服务主管可以作出会商决定")
    issue = ConsultationIssue.objects.select_related("item").get(pk=issue.pk)
    plan = Plan.objects.get(pk=issue.plan_id)
    _bump_revision(plan, expected_revision)
    if issue.status == ConsultationIssue.Status.RESOLVED:
        raise BusinessConflict("该会商议题已有决定")

    issue.status = ConsultationIssue.Status.RESOLVED
    issue.decision = decision
    issue.resolution_note = note
    issue.decided_by = actor
    issue.decided_at = timezone.now()
    issue.save(update_fields=["status", "decision", "resolution_note",
                              "decided_by", "decided_at"])
    # 会商决定只解决矛盾：项目仍处暂停，须由所属条线专业人员重新确认后恢复
    item = issue.item
    if item.state == ItemState.IN_CONFLICT:
        item.state = ItemState.PAUSED
        item.save(update_fields=["state"])
    _log(plan, ChangeAction.RESOLVE, actor, item=item, issue_id=issue.id,
         decision=decision, note=note[:255])
    return issue


@guarded_atomic
def publish_version(*, actor, plan, note="", expected_revision=None):
    """发布不可变版本：快照全部项目并绑定当时有效的全部专业意见。"""
    profile = _profile(actor)
    _require_staff(profile)
    plan = Plan.objects.get(pk=plan.pk)
    _bump_revision(plan, expected_revision)

    number = plan.versions.count() + 1
    # 版本绑定“发布前一刻”的安排，记录的是旧 revision（修订号刚被 +1）
    version = PlanVersion.objects.create(
        plan=plan, number=number, revision=expected_revision, note=note,
        published_by=actor,
    )

    for item in plan.items.all():
        VersionItem.objects.create(
            version=version, source=item, code=item.code, name=item.name,
            discipline=item.discipline, scheduled_time=item.scheduled_time,
            optional=item.optional, state=item.state,
        )
    bound = 0
    for opinion in plan.opinions.select_related("author").all():
        VersionOpinion.objects.create(
            version=version, source=opinion,
            author_name=opinion.author.get_full_name() or opinion.author.username,
            discipline=opinion.discipline, opinion_type=opinion.opinion_type,
            content=opinion.content, created_at=opinion.created_at,
        )
        bound += 1
    plan.current_version = version
    plan.save(update_fields=["current_version"])
    _log(plan, ChangeAction.PUBLISH, actor, version_number=number,
         bound_revision=expected_revision, opinion_count=bound)
    return version
