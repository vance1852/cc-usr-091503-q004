"""业务逻辑层：所有计划变更都经过这里，保证

1. 乐观锁并发控制（compare-and-swap revision，冲突返回 409）；
2. 每次变更生成新的 PlanVersion，绑定当时有效的专业意见并快照项目状态；
3. 不适 / 禁忌可立即暂停相关项目；恢复必须由有权限的专业人员确认；
4. 跨专业意见矛盾时自动进入会商状态并记录尚缺的决定。
"""

from contextlib import contextmanager

from django.db import transaction
from django.utils import timezone

from .models import (
    CarePlan,
    CompletionRecord,
    Consultation,
    Discipline,
    ItemEvent,
    PlanItem,
    PlanVersion,
    ProfessionalOpinion,
    Refusal,
    Role,
)


class DomainError(Exception):
    """业务规则校验失败（对应 HTTP 400）。"""


class RevisionConflict(Exception):
    """base_revision 与当前版本不一致（对应 HTTP 409）。"""

    def __init__(self, current_revision):
        super().__init__("计划已被他人变更，请刷新后重试")
        self.current_revision = current_revision


def active_opinions(plan):
    return plan.opinions.filter(status=ProfessionalOpinion.Status.ACTIVE)


def _items_snapshot(plan):
    return [
        {
            "id": item.id,
            "title": item.title,
            "discipline": item.discipline,
            "status": item.status,
            "optional": item.optional,
            "pause_reason": item.pause_reason,
        }
        for item in plan.items.all()
    ]


@contextmanager
def plan_mutation(plan_id, base_revision, actor, reason):
    """计划变更上下文：CAS 占用 revision，成功后生成新版本。

    使用条件更新（filter(pk, revision).update）实现原子 compare-and-swap，
    在 SQLite 上也能保证并发下只有一个变更成功。
    """
    if base_revision is None:
        raise DomainError("必须提供 base_revision 以防止并发覆盖")
    with transaction.atomic():
        updated = CarePlan.objects.filter(pk=plan_id, revision=base_revision).update(
            revision=base_revision + 1
        )
        if not updated:
            current = CarePlan.objects.values_list("revision", flat=True).get(pk=plan_id)
            raise RevisionConflict(current)
        plan = CarePlan.objects.get(pk=plan_id)
        yield plan
        version = PlanVersion.objects.create(
            plan=plan, number=plan.revision, reason=reason, created_by=actor,
            items_snapshot=_items_snapshot(plan),
        )
        version.opinions.set(active_opinions(plan))


def create_plan(mother, date, created_by):
    plan = CarePlan.objects.create(mother=mother, date=date)
    version = PlanVersion.objects.create(
        plan=plan, number=0, reason="创建计划", created_by=created_by, items_snapshot=[]
    )
    version.opinions.set([])
    return plan


def add_item(plan, actor, base_revision, *, discipline, title, detail="", optional=False):
    if discipline not in Discipline.values:
        raise DomainError("无效的专业领域")
    if not title:
        raise DomainError("项目名称不能为空")
    if actor.discipline != discipline:
        raise DomainError("只能添加本专业的项目")
    with plan_mutation(plan.id, base_revision, actor, f"新增项目：{title}") as plan:
        item = PlanItem.objects.create(
            plan=plan, discipline=discipline, title=title, detail=detail, optional=optional
        )
        ItemEvent.objects.create(
            plan=plan, item=item, actor=actor, action=ItemEvent.Action.CREATED,
            reason=f"新增{dict(Discipline.choices)[discipline]}项目「{title}」",
        )
        return item


def _pause_item(plan, item, actor, reason, opinion=None):
    if item.status == PlanItem.Status.PAUSED:
        item.pause_reason = reason
        item.save(update_fields=["pause_reason"])
        return
    if item.status in (PlanItem.Status.COMPLETED, PlanItem.Status.REFUSED):
        return
    item.status = PlanItem.Status.PAUSED
    item.pause_reason = reason
    item.paused_at = timezone.now()
    item.save(update_fields=["status", "pause_reason", "paused_at"])
    ItemEvent.objects.create(
        plan=plan, item=item, actor=actor, action=ItemEvent.Action.PAUSED,
        reason=reason, opinion=opinion,
    )


def pause_item(plan, item, actor, base_revision, reason):
    if not reason:
        raise DomainError("暂停必须填写原因")
    with plan_mutation(plan.id, base_revision, actor, f"暂停项目：{item.title}") as locked_plan:
        item = locked_plan.items.get(pk=item.pk)
        _pause_item(locked_plan, item, actor, reason)
    return item


def submit_opinion(
    plan, actor, base_revision, *, kind, content, item_ids=(), supersedes_id=None,
    pause_item_ids=None,
):
    """提交专业意见。禁忌默认立即暂停关联项目；评估可显式指定暂停。"""
    if not actor.is_professional:
        raise DomainError("只有护理、营养、康复、心理支持人员可以提交专业意见")
    if kind not in ProfessionalOpinion.Kind.values:
        raise DomainError("无效的意见类型")
    if not content:
        raise DomainError("意见内容不能为空")
    discipline = actor.discipline
    items = list(plan.items.filter(id__in=item_ids))
    if len(items) != len(set(item_ids)):
        raise DomainError("意见关联的项目不存在或不属于该计划")

    supersedes = None
    if supersedes_id is not None:
        supersedes = plan.opinions.filter(id=supersedes_id, status=ProfessionalOpinion.Status.ACTIVE).first()
        if supersedes is None:
            raise DomainError("被取代的意见不存在或已失效")
        if supersedes.discipline != discipline:
            raise DomainError("只能取代本专业之前提交的意见")

    # 禁忌默认暂停所有关联项目；其他类型按显式指定的 pause_item_ids 暂停。
    if pause_item_ids is None:
        to_pause = items if kind == ProfessionalOpinion.Kind.CONTRAINDICATION else []
    else:
        to_pause = list(plan.items.filter(id__in=pause_item_ids))

    with plan_mutation(plan.id, base_revision, actor, f"提交{dict(ProfessionalOpinion.Kind.choices)[kind]}意见") as plan:
        opinion = ProfessionalOpinion.objects.create(
            plan=plan, author=actor, discipline=discipline, kind=kind,
            content=content, supersedes=supersedes,
        )
        opinion.items.set(items)
        if supersedes is not None:
            supersedes.status = ProfessionalOpinion.Status.SUPERSEDED
            supersedes.save(update_fields=["status"])

        for item in to_pause:
            _pause_item(plan, item, actor, f"{dict(ProfessionalOpinion.Kind.choices)[kind]}：{content}", opinion)

        conflicts = _detect_conflicts(plan, opinion)
        for conflicting, shared_items in conflicts:
            _open_consultation(plan, actor, opinion, conflicting, shared_items)
        return opinion


def _detect_conflicts(plan, opinion):
    """检测跨专业矛盾：同一项目上，一个专业建议执行而另一个专业给出禁忌。"""
    kinds = (ProfessionalOpinion.Kind.RECOMMENDATION, ProfessionalOpinion.Kind.CONTRAINDICATION)
    if opinion.kind not in kinds:
        return []
    opposite = (
        ProfessionalOpinion.Kind.CONTRAINDICATION
        if opinion.kind == ProfessionalOpinion.Kind.RECOMMENDATION
        else ProfessionalOpinion.Kind.RECOMMENDATION
    )
    items = list(opinion.items.all())
    if not items:
        return []
    conflicting = (
        ProfessionalOpinion.objects.filter(
            plan=plan, kind=opposite, status=ProfessionalOpinion.Status.ACTIVE, items__in=items
        )
        .exclude(discipline=opinion.discipline)
        .distinct()
    )
    result = []
    for other in conflicting:
        shared = [i for i in items if other.items.filter(id=i.id).exists()]
        if shared:
            result.append((other, shared))
    return result


def _open_consultation(plan, actor, opinion, conflicting, shared_items):
    """开启会商：计划进入会商状态，相关项目保持暂停，明确尚缺的决定。"""
    titles = "、".join(f"「{i.title}」" for i in shared_items)
    pair = sorted([opinion.discipline, conflicting.discipline])
    consultation = Consultation.objects.create(
        plan=plan,
        summary=(
            f"{dict(Discipline.choices)[conflicting.discipline]}的"
            f"{conflicting.get_kind_display()}与{dict(Discipline.choices)[opinion.discipline]}的"
            f"{opinion.get_kind_display()}在{titles}上相互矛盾"
        ),
        pending_decisions=[
            {
                "key": f"item-{item.id}:{'-vs-'.join(pair)}",
                "question": (
                    f"项目「{item.title}」：{dict(Discipline.choices)[pair[0]]}与"
                    f"{dict(Discipline.choices)[pair[1]]}意见矛盾，需明确该项目是否继续、如何调整"
                ),
                "needed_from": pair,
            }
            for item in shared_items
        ],
    )
    consultation.opinions.set([opinion, conflicting])
    plan.status = CarePlan.Status.CONSULTATION
    plan.save(update_fields=["status"])
    for item in shared_items:
        _pause_item(plan, item, actor, "跨专业意见矛盾，待会商", opinion)
    return consultation


def _item_has_open_consultation(plan, item):
    return Consultation.objects.filter(
        plan=plan, status=Consultation.Status.OPEN, opinions__items=item
    ).exists()


def _item_active_contraindications(plan, item):
    return ProfessionalOpinion.objects.filter(
        plan=plan, items=item, kind=ProfessionalOpinion.Kind.CONTRAINDICATION,
        status=ProfessionalOpinion.Status.ACTIVE,
    )


def resume_item(plan, item, actor, base_revision, *, note, supersede_opinion_ids=()):
    """恢复执行：必须由该项目所属专业的人员（或服务主管）重新确认。"""
    allowed = actor.role == Role.SUPERVISOR or actor.discipline == item.discipline
    if not allowed:
        raise DomainError("恢复执行必须由该项目所属专业的人员或服务主管确认")
    if not note:
        raise DomainError("恢复必须填写确认说明")

    with plan_mutation(plan.id, base_revision, actor, f"恢复项目：{item.title}") as locked_plan:
        item = locked_plan.items.get(pk=item.pk)
        if item.status != PlanItem.Status.PAUSED:
            raise DomainError("只有已暂停的项目才能恢复")
        if _item_has_open_consultation(locked_plan, item):
            raise DomainError("该项目涉及未解决的会商，会商结束前不能恢复")
        to_supersede = list(
            locked_plan.opinions.filter(
                id__in=supersede_opinion_ids, status=ProfessionalOpinion.Status.ACTIVE
            )
        )
        for old in to_supersede:
            if old.discipline != item.discipline and actor.role != Role.SUPERVISOR:
                raise DomainError("只能由对应专业或服务主管撤销其禁忌意见")
        confirmation = ProfessionalOpinion.objects.create(
            plan=locked_plan, author=actor, discipline=item.discipline,
            kind=ProfessionalOpinion.Kind.ASSESSMENT, content=f"恢复确认：{note}",
        )
        confirmation.items.set([item])
        for old in to_supersede:
            old.status = ProfessionalOpinion.Status.SUPERSEDED
            old.save(update_fields=["status"])
        if _item_active_contraindications(locked_plan, item).exists():
            raise DomainError("该项目仍存在有效禁忌，需先由对应专业撤销禁忌后才能恢复")
        item.status = PlanItem.Status.SCHEDULED
        item.pause_reason = ""
        item.paused_at = None
        item.save(update_fields=["status", "pause_reason", "paused_at"])
        ItemEvent.objects.create(
            plan=locked_plan, item=item, actor=actor, action=ItemEvent.Action.RESUMED,
            reason=note, opinion=confirmation,
        )
    return item


def complete_item(plan, item, actor, base_revision, note=""):
    """追加完成记录。完成记录只追加，不因后来调整而消失。"""
    allowed = actor.role == Role.SUPERVISOR or actor.discipline == item.discipline
    if not allowed:
        raise DomainError("只能由该项目所属专业的人员或服务主管记录完成")
    with plan_mutation(plan.id, base_revision, actor, f"完成项目：{item.title}") as locked_plan:
        item = locked_plan.items.get(pk=item.pk)
        if item.status != PlanItem.Status.SCHEDULED:
            raise DomainError("只有已排定的项目才能执行完成")
        record = CompletionRecord.objects.create(plan=locked_plan, item=item, performed_by=actor, note=note)
        item.status = PlanItem.Status.COMPLETED
        item.save(update_fields=["status"])
        ItemEvent.objects.create(
            plan=locked_plan, item=item, actor=actor, action=ItemEvent.Action.COMPLETED,
            reason=note,
        )
    return record


def refuse_item(plan, item, mother, base_revision, *, scope, reason=""):
    """产妇拒绝某项服务：保留拒绝范围与时间。"""
    if mother != plan.mother:
        raise DomainError("只能由产妇本人拒绝")
    if scope not in Refusal.Scope.values:
        raise DomainError("无效的拒绝范围")
    with plan_mutation(plan.id, base_revision, mother, f"产妇拒绝：{item.title}") as locked_plan:
        item = locked_plan.items.get(pk=item.pk)
        if item.status == PlanItem.Status.COMPLETED:
            raise DomainError("已完成的项目不能拒绝")
        refusal = Refusal.objects.create(
            plan=locked_plan, item=item, mother=mother, scope=scope, reason=reason
        )
        if scope != Refusal.Scope.THIS_OCCURRENCE:
            item.status = PlanItem.Status.REFUSED
            item.save(update_fields=["status"])
        ItemEvent.objects.create(
            plan=locked_plan, item=item, actor=mother, action=ItemEvent.Action.REFUSED,
            reason=f"{dict(Refusal.Scope.choices)[scope]}；{reason}".rstrip("；"),
        )
    return refusal


def revoke_refusal(refusal, mother, base_revision):
    """撤销拒绝：只能由产妇本人发起（视图层再次校验），记录保留不删除。"""
    plan = refusal.plan
    with plan_mutation(plan.id, base_revision, mother, f"产妇撤销拒绝：{refusal.item.title}") as plan:
        refusal.revoke(mother)
        item = refusal.item
        if item.status == PlanItem.Status.REFUSED and not item.refusals.filter(
            revoked_at__isnull=True
        ).exists():
            item.status = PlanItem.Status.SCHEDULED
            item.save(update_fields=["status"])
        ItemEvent.objects.create(
            plan=plan, item=item, actor=mother, action=ItemEvent.Action.REFUSAL_REVOKED,
            reason="产妇本人撤销拒绝",
        )
    return refusal


def resolve_consultation(consultation, actor, base_revision, *, decisions, resolution):
    """解决会商：必须对每一项尚缺的决定给出结论。"""
    pending_keys = {d["key"] for d in consultation.pending_decisions}
    decided_keys = {d.get("key") for d in decisions}
    missing = pending_keys - decided_keys
    if missing:
        raise DomainError(f"尚缺决定未给出结论：{sorted(missing)}")
    if not resolution:
        raise DomainError("必须填写会商结论")

    with plan_mutation(
        consultation.plan_id, base_revision, actor, f"会商解决：{consultation.summary[:50]}"
    ) as locked_plan:
        consultation = locked_plan.consultations.get(pk=consultation.pk)
        if consultation.status != Consultation.Status.OPEN:
            raise DomainError("该会商已解决")
        consultation.status = Consultation.Status.RESOLVED
        consultation.resolution = resolution
        consultation.resolved_by = actor
        consultation.resolved_at = timezone.now()
        consultation.decisions = decisions
        consultation.save(
            update_fields=["status", "resolution", "resolved_by", "resolved_at", "decisions"]
        )
        if not locked_plan.consultations.filter(status=Consultation.Status.OPEN).exists():
            locked_plan.status = CarePlan.Status.ACTIVE
            locked_plan.save(update_fields=["status"])
    return consultation
