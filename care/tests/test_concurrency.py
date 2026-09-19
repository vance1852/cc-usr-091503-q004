"""
并发变更测试：
- 顺序场景：两个工作人员基于同一 revision 提交，后提交者必须收到 409；
- 真实线程场景：两个事务同时写同一计划，恰好一方成功、一方收到版本冲突；
- 冲突不会造成 revision 跳号或状态半写。
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APITestCase, APITransactionTestCase

from care import services
from care.models import ItemState, Plan, PlanItem, Profile, Role

User = get_user_model()


def _make_users():
    users = {}
    for username, role in [
        ("mama", Role.MOTHER), ("nurse", Role.NURSE),
        ("nutrition", Role.NUTRITIONIST), ("rehab", Role.REHABILITATOR),
        ("boss", Role.SUPERVISOR),
    ]:
        u = User.objects.create_user(username=username, password="pw123456")
        Profile.objects.create(user=u, role=role)
        users[username] = u
    return users


class StaleRevisionTests(APITestCase):
    def setUp(self):
        self.users_root = _make_users()
        self.plan = services.create_plan(
            actor=self.users_root["nurse"], mother=self.users_root["mama"],
            care_date="2026-09-19", title="并发测试计划",
        )
        services.add_item(
            actor=self.users_root["nurse"], plan=self.plan,
            code="CARE-01", name="伤口换药", discipline="care",
            expected_revision=1,
        )
        services.confirm_item(
            actor=self.users_root["nurse"],
            item=self.plan.items.get(code="CARE-01"),
            expected_revision=2,
        )
        # 当前 revision == 3

    def test_second_writer_with_stale_revision_gets_409(self):
        self.client.force_authenticate(self.users_root["nurse"])
        url = f"/api/plans/{self.plan.id}/items/"
        r1 = self.client.post(url, {
            "code": "CARE-02", "name": "会阴擦洗", "discipline": "care",
            "expected_revision": 3,
        }, format="json")
        self.assertEqual(r1.status_code, 201)

        r2 = self.client.post(url, {
            "code": "CARE-03", "name": "红外照射", "discipline": "care",
            "expected_revision": 3,  # 已过期
        }, format="json")
        self.assertEqual(r2.status_code, 409)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.revision, 4)
        self.assertEqual(self.plan.items.count(), 2)

    def test_concurrent_pause_and_complete_same_revision(self):
        item = self.plan.items.get(code="CARE-01")
        # 营养师先暂停（立即生效）
        services.pause_item(
            actor=self.users_root["nutrition"], item=item,
            reason="临时饮食相关观察", expected_revision=3,
        )
        # 护理师仍基于 revision=3 登记完成 -> 409；即使刷新后再试，
        # 项目已处会商状态，完成被业务规则拒绝
        with self.assertRaises(services.RevisionConflict):
            services.complete_item(
                actor=self.users_root["nurse"], item=item,
                comment="基于旧页面的完成登记", expected_revision=3,
            )


class ThreadedConcurrencyTests(APITransactionTestCase):
    """真实多事务并发：SQLite IMMEDIATE 下一方排队，条件 UPDATE 判定过期。"""

    def setUp(self):
        self.users = _make_users()
        self.plan = services.create_plan(
            actor=self.users["nurse"], mother=self.users["mama"],
            care_date="2026-09-19", title="线程并发计划",
        )
        services.add_item(
            actor=self.users["rehab"], plan=self.plan,
            code="REHAB-01", name="盆底训练", discipline="rehab",
            expected_revision=1,
        )
        services.confirm_item(
            actor=self.users["rehab"],
            item=self.plan.items.get(code="REHAB-01"),
            expected_revision=2,
        )
        # 当前 revision == 3，两个线程都基于 3 提交

    def _thread_complete(self, outcome, idx):
        try:
            item = PlanItem.objects.get(code="REHAB-01")
            try:
                services.complete_item(
                    actor=self.users["rehab"], item=item,
                    comment=f"线程{idx}完成登记", expected_revision=3,
                )
                outcome[idx] = ("ok", None)
            except services.RevisionConflict as exc:
                outcome[idx] = ("conflict", str(exc))
        except Exception as exc:  # 任何意外都记录下来
            outcome[idx] = ("error", repr(exc))
        finally:
            connection.close()

    def test_parallel_writes_exactly_one_wins(self):
        outcome = {}
        barrier = threading.Barrier(2)

        def runner(idx):
            barrier.wait()
            self._thread_complete(outcome, idx)

        t1 = threading.Thread(target=runner, args=(1,))
        t2 = threading.Thread(target=runner, args=(2,))
        t1.start(); t2.start()
        t1.join(30); t2.join(30)

        results = [outcome.get(1), outcome.get(2)]
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, ["conflict", "ok"], msg=results)

        self.plan.refresh_from_db()
        # 只有一次成功提交：revision 恰好 +1，完成记录恰好 1 条
        self.assertEqual(self.plan.revision, 4)
        item = self.plan.items.get(code="REHAB-01")
        self.assertEqual(item.completions.count(), 1)

    def test_parallel_pauses_second_is_rejected_or_idempotent_conflict(self):
        outcome = {}
        barrier = threading.Barrier(2)

        def pause(idx, actor_key):
            barrier.wait()
            try:
                item = PlanItem.objects.get(code="REHAB-01")
                try:
                    services.pause_item(
                        actor=self.users[actor_key], item=item,
                        reason=f"线程{idx}发现不适", expected_revision=3,
                    )
                    outcome[idx] = ("ok", None)
                except (services.RevisionConflict, services.BusinessConflict) as exc:
                    outcome[idx] = ("conflict", str(exc))
            except Exception as exc:
                outcome[idx] = ("error", repr(exc))
            finally:
                connection.close()

        t1 = threading.Thread(target=pause, args=(1, "rehab"))
        t2 = threading.Thread(target=pause, args=(2, "nurse"))
        t1.start(); t2.start()
        t1.join(30); t2.join(30)

        results = [outcome.get(1), outcome.get(2)]
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, ["conflict", "ok"], msg=results)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.revision, 4)
        item = self.plan.items.get(code="REHAB-01")
        self.assertIn(item.state,
                      (ItemState.PAUSED, ItemState.IN_CONFLICT))
        self.assertEqual(item.pause_events.count(), 1)
