"""计划版本：每次变更生成新版本，并绑定当时有效的专业意见。"""

from care.models import PlanVersion, ProfessionalOpinion

from .base import CareApiTestCase


class PlanVersionBindingTest(CareApiTestCase):
    def test_every_mutation_creates_version_bound_to_active_opinions(self):
        # setUp 中已创建计划（v0）并添加四个项目（v1~v4）。
        self.assertEqual(self.plan.versions.count(), 5)
        v0 = self.plan.versions.get(number=0)
        self.assertEqual(v0.opinions.count(), 0)

        # 护理提交评估 A。
        resp = self.submit_opinion(self.nurse, kind="ASSESSMENT", content="伤口愈合良好")
        opinion_a_id = resp.data["id"]
        v = self.plan.versions.get(number=self.rev())
        self.assertEqual(
            set(v.opinions.values_list("id", flat=True)), {opinion_a_id}
        )

        # 护理提交评估 B 取代 A：新版本只绑定有效的 B。
        resp = self.submit_opinion(
            self.nurse, kind="ASSESSMENT", content="伤口已结痂",
            supersedes_id=opinion_a_id,
        )
        opinion_b_id = resp.data["id"]
        v_latest = self.plan.versions.get(number=self.rev())
        self.assertEqual(
            set(v_latest.opinions.values_list("id", flat=True)), {opinion_b_id}
        )
        self.assertEqual(
            ProfessionalOpinion.objects.get(id=opinion_a_id).status, "SUPERSEDED"
        )

        # 历史版本仍然绑定当时的有效意见 A，不随后续调整改变。
        v_old = self.plan.versions.get(number=v.number)
        self.assertEqual(
            set(v_old.opinions.values_list("id", flat=True)), {opinion_a_id}
        )

    def test_version_snapshots_item_status_at_the_time(self):
        self.submit_opinion(
            self.nurse,
            kind="ASSESSMENT",
            content="伤口不适，暂停",
            item_ids=[self.wound.id],
            pause_item_ids=[self.wound.id],
        )
        v = self.plan.versions.get(number=self.rev())
        snapshot = {i["id"]: i for i in v.items_snapshot}
        self.assertEqual(snapshot[self.wound.id]["status"], "PAUSED")
        self.assertIn("伤口不适", snapshot[self.wound.id]["pause_reason"])
        self.assertEqual(snapshot[self.meal.id]["status"], "SCHEDULED")

    def test_versions_api_lists_binding(self):
        self.submit_opinion(self.nurse, kind="ASSESSMENT", content="常规评估")
        resp = self.client_for(self.boss).get(f"/api/plans/{self.plan.id}/versions/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), self.rev() + 1)
        latest = resp.data[-1]
        self.assertEqual(latest["number"], self.rev())
        self.assertEqual(len(latest["opinions"]), 1)
        self.assertEqual(latest["opinions"][0]["content"], "常规评估")

    def test_versions_are_append_only(self):
        from django.core.exceptions import ValidationError

        v = self.plan.versions.get(number=0)
        with self.assertRaises(ValidationError):
            v.save()
        with self.assertRaises(ValidationError):
            v.delete()
        self.assertTrue(PlanVersion.objects.filter(pk=v.pk).exists())
