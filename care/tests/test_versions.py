"""计划版本：版本快照不可变，且绑定发布时点的全部有效专业意见。"""
from care import services
from care.models import (
    ChangeAction,
    ItemState,
    OpinionType,
    VersionOpinion,
)
from care.tests.base import CareAPITestCase


class VersionBindingTests(CareAPITestCase):
    def test_version_snapshots_items_and_binds_opinions(self):
        item = self.add_item("REHAB-01", "盆底肌训练", "rehab", actor=self.rehab)
        self.confirm(item, actor=self.rehab)
        services.submit_opinion(
            actor=self.rehab, plan=self.plan,
            opinion_type=OpinionType.ASSESSMENT,
            content="盆底肌张力2级，可轻度训练", item=item,
            expected_revision=self.rev,
        )
        self.rev += 1

        v1 = services.publish_version(
            actor=self.nurse, plan=self.plan, note="早班发布",
            expected_revision=self.rev,
        )
        self.rev += 1

        self.assertEqual(v1.number, 1)
        self.assertEqual(v1.items.count(), 1)
        # 确认安排(RECOMMENDATION) + 康复评估(ASSESSMENT) 两条意见均被绑定
        self.assertEqual(v1.opinion_bindings.count(), 2)
        snap = v1.items.get()
        self.assertEqual(snap.state, ItemState.CONFIRMED)
        self.assertEqual(snap.name, "盆底肌训练")

        # 版本 1 之后：跨条线禁忌 -> 项目进入会商，再发版本 2
        services.submit_opinion(
            actor=self.nurse, plan=self.plan,
            opinion_type=OpinionType.CONTRAINDICATION,
            content="会阴伤口红肿，暂停盆底训练", item=item,
            expected_revision=self.rev,
        )
        self.rev += 1
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.IN_CONFLICT)

        v2 = services.publish_version(
            actor=self.nurse, plan=self.plan, note="出现禁忌后发布",
            expected_revision=self.rev,
        )
        self.rev += 1

        # 旧版本快照不被后来的状态变化影响
        self.assertEqual(v1.items.get().state, ItemState.CONFIRMED)
        self.assertEqual(v2.items.get().state, ItemState.IN_CONFLICT)
        # 版本 1 绑定 2 条意见，版本 2 绑定当时全部 3 条
        self.assertEqual(v1.opinion_bindings.count(), 2)
        self.assertEqual(v2.opinion_bindings.count(), 3)
        bound = VersionOpinion.objects.filter(version=v2, discipline="care").get()
        self.assertIn("会阴伤口红肿", bound.content)

    def test_every_publish_is_audited(self):
        self.add_item("PSY-01", "情绪疏导", "psych", actor=self.psych)
        services.publish_version(
            actor=self.psych, plan=self.plan, expected_revision=self.rev,
        )
        self.rev += 1
        publish_logs = self.plan.changes.filter(action=ChangeAction.PUBLISH)
        self.assertEqual(publish_logs.count(), 1)
        self.assertEqual(publish_logs.first().detail["version_number"], 1)
        self.assertEqual(publish_logs.first().detail["opinion_count"], 0)

    def test_publish_requires_expected_revision(self):
        self.login(self.nurse)
        resp = self.client.post(f"/api/plans/{self.plan.id}/publish/",
                                {"note": "缺 revision"}, format="json")
        # 缺少必填的 expected_revision：请求校验不通过
        self.assertEqual(resp.status_code, 400)
