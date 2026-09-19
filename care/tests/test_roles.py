"""角色权限：六类账号各自能做什么、不能做什么。"""
from care.models import ItemState, Role
from care.tests.base import CareAPITestCase


class RolePermissionTests(CareAPITestCase):
    # ---- 未登录 / 越权访问 ------------------------------------------------

    def test_anonymous_rejected(self):
        resp = self.client.get("/api/plans/")
        self.assertEqual(resp.status_code, 401)

    def test_mother_only_sees_own_plans(self):
        self.login(self.other_mother)
        resp = self.client.get("/api/plans/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"] if "results" in resp.data else resp.data, [])

        self.login(self.mother)
        resp = self.client.get("/api/plans/")
        data = resp.data["results"] if "results" in resp.data else resp.data
        self.assertEqual(len(data), 1)

    def test_mother_cannot_create_plan_or_items(self):
        self.login(self.mother)
        resp = self.client.post("/api/plans/", {
            "mother_username": "mama", "care_date": "2026-09-19",
            "title": "自建立计划",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

        resp = self.client.post(f"/api/plans/{self.plan.id}/items/", {
            "code": "X", "name": "x", "discipline": "care",
            "expected_revision": 1,
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_mother_cannot_submit_opinion_or_pause(self):
        item = self.add_item("CARE-01", "伤口换药", "care")
        self.login(self.mother)
        resp = self.client.post(f"/api/plans/{self.plan.id}/opinions/", {
            "opinion_type": "ASSESSMENT", "content": "我觉得没事",
            "expected_revision": self.rev,
        }, format="json")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(f"/api/plans/{self.plan.id}/items/{item.id}/pause/", {
            "reason": "我自己暂停", "expected_revision": self.rev,
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    # ---- 条线权限：只能确认/恢复本条线项目 -------------------------------

    def test_discipline_cannot_confirm_other_discipline_item(self):
        item = self.add_item("REHAB-01", "康复训练", "rehab", actor=self.rehab)
        self.login(self.nutritionist)
        resp = self.client.post(
            f"/api/plans/{self.plan.id}/items/{item.id}/confirm/",
            {"expected_revision": self.rev}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_discipline_owner_can_confirm(self):
        item = self.add_item("REHAB-01", "康复训练", "rehab", actor=self.rehab)
        self.login(self.rehab)
        resp = self.client.post(
            f"/api/plans/{self.plan.id}/items/{item.id}/confirm/",
            {"expected_revision": self.rev}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["state"], ItemState.CONFIRMED)

    def test_resume_requires_item_owner_discipline(self):
        item = self.add_item("REHAB-01", "康复训练", "rehab", actor=self.rehab)
        self.confirm(item, actor=self.rehab)
        self.pause(item, self.rehab, "产妇关节酸痛")
        # 营养师无权恢复康复项目
        self.login(self.nutritionist)
        resp = self.client.post(
            f"/api/plans/{self.plan.id}/items/{item.id}/resume/",
            {"comment": "我看可以恢复", "expected_revision": self.rev},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.state, ItemState.PAUSED)

    def test_any_professional_can_pause_immediately(self):
        # 产妇临时反馈伤口不适：康复师可立即暂停护理项目（但会进入会商）
        item = self.add_item("CARE-01", "红外线照射", "care")
        self.confirm(item)
        self.login(self.rehab)
        resp = self.client.post(
            f"/api/plans/{self.plan.id}/items/{item.id}/pause/",
            {"reason": "伤口不适需暂停照射", "expected_revision": self.rev},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["state"], ItemState.IN_CONFLICT)

    # ---- 会商决定权属于服务主管 ------------------------------------------

    def test_only_supervisor_resolves_issues(self):
        item = self.add_item("CARE-01", "红外线照射", "care")
        self.confirm(item)
        self.pause(item, self.rehab, "伤口不适")
        issue = self.plan.issues.get()

        self.login(self.nurse)
        resp = self.client.post(f"/api/issues/{issue.id}/resolve/", {
            "decision": "PROCEED", "note": "继续",
            "expected_revision": self.rev,
        }, format="json")
        self.assertEqual(resp.status_code, 403)

        self.login(self.supervisor)
        resp = self.client.post(f"/api/issues/{issue.id}/resolve/", {
            "decision": "PROCEED", "note": "会商后允许恢复",
            "expected_revision": self.rev,
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "RESOLVED")

    def test_supervisor_open_issues_list(self):
        self.login(self.nutritionist)
        resp = self.client.get("/api/supervisor/issues/open/")
        self.assertEqual(resp.status_code, 403)
        self.login(self.supervisor)
        resp = self.client.get("/api/supervisor/issues/open/")
        self.assertEqual(resp.status_code, 200)
