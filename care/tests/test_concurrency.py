"""并发变更：乐观锁保证同一时刻只有一个变更生效，其余收到 409。"""

import datetime
import threading

from django.test import TransactionTestCase
from rest_framework.test import APIClient

from care import services
from care.models import CarePlan, PlanVersion, ProfessionalOpinion, Role, User


class ConcurrentMutationTest(TransactionTestCase):
    """使用文件型测试库 + TransactionTestCase，让多个线程连接同一数据库。"""

    def setUp(self):
        self.mother = User.objects.create_user("mother1", role=Role.MOTHER)
        self.nurse = User.objects.create_user("nurse1", role=Role.NURSE)
        self.nutritionist = User.objects.create_user("nutrition1", role=Role.NUTRITIONIST)
        self.boss = User.objects.create_user("boss", role=Role.SUPERVISOR)
        self.plan = services.create_plan(self.mother, datetime.date(2026, 9, 19), self.boss)
        self.item = services.add_item(
            self.plan, self.nurse, 0, discipline="NURSING", title="伤口护理"
        )

    def rev(self):
        return CarePlan.objects.values_list("revision", flat=True).get(pk=self.plan.pk)

    def test_stale_base_revision_gets_409(self):
        client = APIClient()
        client.force_authenticate(self.nurse)
        url = f"/api/plans/{self.plan.id}/submit_opinion/"
        payload = {"kind": "ASSESSMENT", "content": "第一次评估", "base_revision": self.rev()}
        self.assertEqual(client.post(url, payload, format="json").status_code, 201)

        # 另一个工作人员仍拿着旧的 base_revision 提交 → 409 并告知当前版本号。
        client2 = APIClient()
        client2.force_authenticate(self.nutritionist)
        stale = dict(payload, content="基于旧版本的提交")
        resp = client2.post(url, stale, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["current_revision"], self.rev())

    def test_missing_base_revision_gets_400(self):
        client = APIClient()
        client.force_authenticate(self.nurse)
        resp = client.post(
            f"/api/plans/{self.plan.id}/submit_opinion/",
            {"kind": "ASSESSMENT", "content": "缺少版本号"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_concurrent_submissions_exactly_one_wins(self):
        """护理与营养同时基于同一版本提交：恰一个成功，版本号只前进一次。"""
        base_revision = self.rev()
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def submit(user, content):
            from django.db import connections

            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                resp = client.post(
                    f"/api/plans/{self.plan.id}/submit_opinion/",
                    {
                        "kind": "ASSESSMENT",
                        "content": content,
                        "base_revision": base_revision,
                    },
                    format="json",
                )
                results.append(resp.status_code)
            except Exception as exc:  # noqa: BLE001 - 测试里原样抛出更直观
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=submit, args=(self.nurse, "护理评估")),
            threading.Thread(target=submit, args=(self.nutritionist, "营养评估")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [201, 409])
        # revision 只 +1，只生成一个新版本、一条意见。
        self.assertEqual(self.rev(), base_revision + 1)
        self.assertEqual(
            PlanVersion.objects.filter(plan=self.plan).count(), base_revision + 2
        )
        self.assertEqual(ProfessionalOpinion.objects.filter(plan=self.plan).count(), 1)

    def test_concurrent_pause_and_complete_no_double_apply(self):
        """暂停与完成并发：只有一个生效，项目状态与版本保持一致。"""
        base_revision = self.rev()
        barrier = threading.Barrier(2)
        results = []

        def act(user, url, payload):
            from django.db import connections

            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                payload = dict(payload, base_revision=base_revision)
                resp = client.post(url, payload, format="json")
                results.append(resp.status_code)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(
                target=act,
                args=(self.boss, f"/api/items/{self.item.id}/pause/", {"reason": "产妇不适"}),
            ),
            threading.Thread(
                target=act,
                args=(self.nurse, f"/api/items/{self.item.id}/complete/", {"note": "已完成"}),
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), 2)
        self.assertIn(409, results)
        self.assertEqual(len([r for r in results if r < 300]), 1)
        self.assertEqual(self.rev(), base_revision + 1)
