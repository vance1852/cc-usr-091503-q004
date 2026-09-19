"""核心业务流程：暂停、恢复确认、会商、禁忌、产妇拒绝、完成记录追加。"""
from django.core.exceptions import PermissionDenied

from care import services
from care.models import (
    ChangeAction,
    CompletionRecord,
    ConsultationIssue,
    ItemState,
    OpinionType,
    Refusal,
)
from care.tests.base import CareAPITestCase


class PauseResumeTests(CareAPITestCase):
    def test_same_discipline_contraindication_pauses_immediately(self):
        item = self.add_item("REHAB-01", "腹直肌修复", "rehab", actor=self.rehab)
        self.confirm(item, actor=self.rehab)
        services.submit_opinion(
            actor=self.rehab, plan=self.plan, item=item,
            opinion_type=OpinionType.CONTRAINDICATION,
            content="产妇恶露量突增，暂停腹部训练",
            expected_revision=self.rev,
        )
        self.rev += 1
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.PAUSED)
        self.assertEqual(item.pause_events.count(), 1)
        self.assertIsNone(item.pause_events.first().cleared_at)

    def test_resume_needs_new_confirmation_from_owner(self):
        item = self.add_item("REHAB-01", "腹直肌修复", "rehab", actor=self.rehab)
        self.confirm(item, actor=self.rehab)
        self.pause(item, self.rehab, "恶露增多")
        # 未确认直接想恢复：必须走恢复接口并留下恢复确认意见
        self.resume(item, self.rehab, comment="复查恶露正常，恢复训练")
        self.assertEqual(item.state, ItemState.CONFIRMED)
        pause = item.pause_events.first()
        self.assertIsNotNone(pause.cleared_at)
        self.assertEqual(pause.cleared_by, self.rehab)
        self.assertTrue(
            item.opinions.filter(opinion_type=OpinionType.CONFIRMATION).exists()
        )

    def test_cross_discipline_conflict_enters_consultation_with_missing_decision(self):
        item = self.add_item("NUTR-01", "红枣桂圆汤", "nutrition",
                             actor=self.nutritionist)
        self.confirm(item, actor=self.nutritionist)
        # 护理师发现伤口情况，对营养项目提出禁忌 -> 会商
        self.login(self.nurse)
        resp = self.client.post(f"/api/plans/{self.plan.id}/opinions/", {
            "opinion_type": "CONTRAINDICATION",
            "content": "会阴伤口愈合不良，桂圆活血需暂停",
            "item": item.id, "expected_revision": self.rev,
        }, format="json")
        self.rev += 1
        self.assertEqual(resp.status_code, 201)
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.IN_CONFLICT)
        issue = self.plan.issues.get()
        self.assertEqual(issue.status, ConsultationIssue.Status.OPEN)
        self.assertIn("尚缺决定", issue.missing_decision)

    def test_resume_blocked_until_consultation_resolved(self):
        item = self.add_item("NUTR-01", "红枣桂圆汤", "nutrition",
                             actor=self.nutritionist)
        self.confirm(item, actor=self.nutritionist)
        self.pause(item, self.nurse, "伤口愈合不良，桂圆活血")
        self.assertEqual(item.state, ItemState.IN_CONFLICT)

        # 会商未决：营养师即使是项目 owner 也不能恢复
        with self.assertRaises(services.BusinessConflict):
            services.resume_item(
                actor=self.nutritionist, item=item, comment="我认为可以喝",
                expected_revision=self.rev,
            )

        # 主管会商决定 PROCEED，但项目仍处暂停，需 owner 重新确认
        issue = self.plan.issues.get()
        services.resolve_issue(
            actor=self.supervisor, issue=issue, decision="PROCEED",
            note="会商：停桂圆一周后恢复", expected_revision=self.rev,
        )
        self.rev += 1
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.PAUSED)

        self.resume(item, self.nutritionist, comment="一周后评估，恢复供应")
        self.assertEqual(item.state, ItemState.CONFIRMED)
        issue.refresh_from_db()
        self.assertEqual(issue.status, ConsultationIssue.Status.RESOLVED)

    def test_halt_decision_keeps_item_paused(self):
        item = self.add_item("NUTR-01", "酒酿圆子", "nutrition",
                             actor=self.nutritionist)
        self.confirm(item, actor=self.nutritionist)
        self.pause(item, self.psych, "情绪相关睡眠差，避免酒精摄入")
        issue = self.plan.issues.get()
        services.resolve_issue(
            actor=self.supervisor, issue=issue, decision="HALT",
            note="会商决定取消含酒精餐食", expected_revision=self.rev,
        )
        self.rev += 1
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.PAUSED)


class RefusalTests(CareAPITestCase):
    def test_mother_refusal_records_scope_and_time(self):
        item = self.add_item("PSY-01", "团体心理疏导", "psych", actor=self.psych,
                             optional=True)
        self.confirm(item, actor=self.psych)

        refusal = services.refuse_item(
            mother=self.mother, item=item, scope="仅今日团体项目",
            reason="今天想多休息", expected_revision=self.rev,
        )
        self.rev += 1
        self.assertEqual(refusal.scope, "仅今日团体项目")
        self.assertIsNotNone(refusal.created_at)
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.REFUSED)

    def test_staff_cannot_refuse_or_cancel_refusal(self):
        item = self.add_item("PSY-01", "团体心理疏导", "psych", actor=self.psych)
        self.confirm(item, actor=self.psych)
        # 工作人员不能代替产妇拒绝
        with self.assertRaises(PermissionDenied):
            services.refuse_item(
                mother=self.nurse, item=item, expected_revision=self.rev,
            )
        services.refuse_item(
            mother=self.mother, item=item, scope="今天不参加",
            expected_revision=self.rev,
        )
        self.rev += 1
        # 任何工作人员都不能暂停/恢复/改期已拒绝项目
        item.refresh_from_db()
        with self.assertRaises(services.BusinessConflict):
            services.pause_item(
                actor=self.psych, item=item, reason="尝试覆盖拒绝",
                expected_revision=self.rev,
            )
        with self.assertRaises(services.BusinessConflict):
            services.resume_item(
                actor=self.psych, item=item, comment="代为恢复",
                expected_revision=self.rev,
            )
        # 拒绝记录仍然只有一条，范围时间保留
        self.assertEqual(Refusal.objects.filter(item=item).count(), 1)
        self.assertEqual(item.refusal.scope, "今天不参加")

    def test_other_mother_cannot_refuse(self):
        item = self.add_item("CARE-01", "乳房护理", "care")
        with self.assertRaises(Exception):
            services.refuse_item(
                mother=self.other_mother, item=item,
                expected_revision=self.rev,
            )


class CompletionTests(CareAPITestCase):
    def test_completions_are_append_only(self):
        item = self.add_item("REHAB-01", "凯格尔训练", "rehab", actor=self.rehab)
        self.confirm(item, actor=self.rehab)
        r1 = services.complete_item(
            actor=self.rehab, item=item, comment="上午第1组完成",
            expected_revision=self.rev,
        )
        self.rev += 1
        r2 = services.complete_item(
            actor=self.rehab, item=item, comment="下午第2组完成",
            expected_revision=self.rev,
        )
        self.rev += 1
        self.assertEqual(CompletionRecord.objects.filter(item=item).count(), 2)

        # 后来项目被暂停：历史完成记录不消失
        self.pause(item, self.rehab, "伤口不适，暂停训练")
        self.assertEqual(CompletionRecord.objects.filter(item=item).count(), 2)
        ids = list(CompletionRecord.objects.filter(item=item)
                   .order_by("id").values_list("id", flat=True))
        self.assertEqual(ids, [r1.id, r2.id])
        # 暂停状态不能继续登记完成
        with self.assertRaises(services.BusinessConflict):
            services.complete_item(
                actor=self.rehab, item=item, expected_revision=self.rev,
            )
        # 完成动作均有审计
        self.assertEqual(
            self.plan.changes.filter(action=ChangeAction.COMPLETE).count(), 2
        )


class ScenarioTests(CareAPITestCase):
    """题目场景：执行计划当天，伤口不适 + 营养师调整禁忌，原计划不再显示全项目可做。"""

    def test_full_day_scenario(self):
        wound = self.add_item("CARE-01", "会阴伤口换药", "care",
                              scheduled=None)
        meal = self.add_item("NUTR-01", "滋补月子餐", "nutrition",
                             actor=self.nutritionist)
        train = self.add_item("REHAB-01", "盆底肌训练", "rehab", actor=self.rehab)

        self.confirm(wound)
        self.confirm(meal, actor=self.nutritionist)
        self.confirm(train, actor=self.rehab)

        # 产妇临时反馈伤口不适，康复师立刻暂停康复项目（同条线暂停）
        self.pause(train, self.rehab, "产妇反映伤口疼痛，训练暂停")

        # 营养师刚调整饮食禁忌
        services.submit_opinion(
            actor=self.nutritionist, plan=self.plan, item=meal,
            opinion_type=OpinionType.CONTRAINDICATION,
            content="新增禁忌：停用当归类活血食材",
            expected_revision=self.rev,
        )
        self.rev += 1
        meal.refresh_from_db()
        self.assertEqual(meal.state, ItemState.PAUSED)

        # 产妇端当天视图：只剩换药已确认，其余带暂停原因
        self.login(self.mother)
        resp = self.client.get("/api/mother/today/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({i["code"] for i in resp.data["confirmed"]}, {"CARE-01"})
        paused_codes = {i["code"] for i in resp.data["paused"]}
        self.assertEqual(paused_codes, {"NUTR-01", "REHAB-01"})
        for item in resp.data["paused"]:
            self.assertIsNotNone(item["pause_reason"])

        # 恢复：营养师重新确认后餐食才可恢复
        self.resume(meal, self.nutritionist, comment="更换菜单去除活血食材")
        resp = self.client.get("/api/mother/today/")
        self.assertEqual(
            {i["code"] for i in resp.data["confirmed"]}, {"CARE-01", "NUTR-01"}
        )

        # 主管可追溯所有变更依据
        self.login(self.supervisor)
        resp = self.client.get(f"/api/plans/{self.plan.id}/changes/")
        self.assertEqual(resp.status_code, 200)
        actions = {c["action"] for c in resp.data}
        self.assertIn("PAUSE", actions)
        self.assertIn("OPINION", actions)
        self.assertIn("RESUME", actions)
