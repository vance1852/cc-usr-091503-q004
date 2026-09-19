"""角色权限：四类专业人员、产妇、服务主管各自的能与不能。"""

from care.models import PlanItem, Refusal

from .base import CareApiTestCase


class RolePermissionTest(CareApiTestCase):
    def test_mother_cannot_submit_professional_opinion(self):
        resp = self.submit_opinion(self.mother, kind="ASSESSMENT", content="我来评估")
        self.assertEqual(resp.status_code, 403)

    def test_supervisor_cannot_submit_professional_opinion(self):
        resp = self.submit_opinion(self.boss, kind="ASSESSMENT", content="主管代写")
        self.assertEqual(resp.status_code, 403)

    def test_professional_cannot_add_item_of_other_discipline(self):
        resp = self.client_for(self.nutritionist).post(
            f"/api/plans/{self.plan.id}/add_item/",
            {"base_revision": self.rev(), "discipline": "NURSING", "title": "越界项目"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_opinion_discipline_is_taken_from_author_role(self):
        resp = self.submit_opinion(
            self.nutritionist, kind="RECOMMENDATION", content="增加粗粮",
            item_ids=[self.meal.id],
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["discipline"], "NUTRITION")

    def test_mother_sees_only_own_plans(self):
        client = self.client_for(self.mother2)
        resp = client.get("/api/plans/")
        self.assertEqual(resp.data, [])
        # 其他产妇的计划对其不可见（不暴露存在性）。
        resp = client.get(f"/api/plans/{self.plan.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_staff_cannot_access_audit_view(self):
        resp = self.client_for(self.nurse).get(f"/api/plans/{self.plan.id}/audit/")
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_rejected(self):
        from rest_framework.test import APIClient

        resp = APIClient().get("/api/plans/")
        self.assertIn(resp.status_code, (401, 403))


class RefusalTest(CareApiTestCase):
    def _refuse(self, scope="TODAY"):
        return self.client_for(self.mother).post(
            f"/api/items/{self.counseling.id}/refuse/",
            {"base_revision": self.rev(), "scope": scope, "reason": "今天不想谈"},
            format="json",
        )

    def test_refusal_records_scope_and_time(self):
        resp = self._refuse(scope="TODAY")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["scope"], "TODAY")
        self.assertIsNotNone(resp.data["created_at"])
        self.counseling.refresh_from_db()
        self.assertEqual(self.counseling.status, PlanItem.Status.REFUSED)

    def test_refusal_cannot_be_revoked_by_others(self):
        refusal_id = self._refuse().data["id"]
        # 服务主管也不能代为取消。
        resp = self.client_for(self.boss).post(
            f"/api/refusals/{refusal_id}/revoke/",
            {"base_revision": self.rev()},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("产妇本人", resp.data["detail"])
        # 其他工作人员同样不行。
        resp = self.client_for(self.psych).post(
            f"/api/refusals/{refusal_id}/revoke/",
            {"base_revision": self.rev()},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        refusal = Refusal.objects.get(id=refusal_id)
        self.assertTrue(refusal.is_active)

    def test_mother_can_revoke_own_refusal_and_record_is_kept(self):
        refusal_id = self._refuse().data["id"]
        resp = self.client_for(self.mother).post(
            f"/api/refusals/{refusal_id}/revoke/",
            {"base_revision": self.rev()},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        # 记录保留（范围与时间仍在），只是标记撤销。
        refusal = Refusal.objects.get(id=refusal_id)
        self.assertFalse(refusal.is_active)
        self.assertIsNotNone(refusal.revoked_at)
        self.assertEqual(refusal.scope, "TODAY")
        self.counseling.refresh_from_db()
        self.assertEqual(self.counseling.status, PlanItem.Status.SCHEDULED)

    def test_mother_cannot_refuse_for_other_mother(self):
        resp = self.client_for(self.mother2).post(
            f"/api/items/{self.counseling.id}/refuse/",
            {"base_revision": self.rev(), "scope": "ITEM"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_staff_cannot_refuse_on_behalf(self):
        resp = self.client_for(self.boss).post(
            f"/api/items/{self.counseling.id}/refuse/",
            {"base_revision": self.rev(), "scope": "ITEM"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
