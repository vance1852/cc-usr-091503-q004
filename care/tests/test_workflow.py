"""端到端工作流：对应题目场景——

康复师准备执行当天计划时，产妇反馈伤口不适，营养师又调整了饮食禁忌；
系统应立即暂停相关项目、在跨专业意见矛盾时进入会商，恢复必须由有权限的
专业人员重新确认，产妇端与主管端看到各自视图。
"""

from care.models import CarePlan, Consultation, PlanItem, ProfessionalOpinion

from .base import CareApiTestCase


class DailyPlanWorkflowTest(CareApiTestCase):
    def test_discomfort_pauses_item_and_today_view_reflects_it(self):
        # 产妇临时反馈伤口不适，护理提交评估并立即暂停伤口护理项目。
        resp = self.submit_opinion(
            self.nurse,
            kind="ASSESSMENT",
            content="产妇主诉伤口疼痛加剧，暂停伤口护理",
            item_ids=[self.wound.id],
            pause_item_ids=[self.wound.id],
        )
        self.assertEqual(resp.status_code, 201)

        self.wound.refresh_from_db()
        self.assertEqual(self.wound.status, PlanItem.Status.PAUSED)
        self.assertIn("伤口疼痛", self.wound.pause_reason)

        # 产妇端当天视图：暂停项目不再出现在已确认安排中，且能看到暂停原因。
        resp = self.client_for(self.mother).get(f"/api/plans/{self.plan.id}/today/")
        self.assertEqual(resp.status_code, 200)
        confirmed_ids = [i["id"] for i in resp.data["confirmed"]]
        self.assertNotIn(self.wound.id, confirmed_ids)
        self.assertIn(self.exercise.id, confirmed_ids)
        # 可选择事项：可选且已排定的项目。
        option_ids = [i["id"] for i in resp.data["options"]]
        self.assertEqual(set(option_ids), {self.meal.id, self.counseling.id})
        paused = resp.data["paused"]
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0]["id"], self.wound.id)
        self.assertIn("伤口疼痛", paused[0]["pause_reason"])

    def test_contraindication_auto_pauses_and_conflicts_into_consultation(self):
        # 康复师建议高蛋白饮食配合训练（关联月子餐项目）。
        resp = self.submit_opinion(
            self.rehab,
            kind="RECOMMENDATION",
            content="建议高蛋白饮食配合康复训练",
            item_ids=[self.meal.id],
        )
        self.assertEqual(resp.status_code, 201)

        # 营养师随后调整饮食禁忌：忌高蛋白。自动暂停月子餐，并与康复建议冲突。
        resp = self.submit_opinion(
            self.nutritionist,
            kind="CONTRAINDICATION",
            content="肾功能指标异常，禁忌高蛋白饮食",
            item_ids=[self.meal.id],
        )
        self.assertEqual(resp.status_code, 201)

        self.meal.refresh_from_db()
        self.assertEqual(self.meal.status, PlanItem.Status.PAUSED)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, CarePlan.Status.CONSULTATION)

        # 会商单明确了尚缺的决定及需要哪些专业参与。
        consultation = Consultation.objects.get(plan=self.plan, status="OPEN")
        self.assertEqual(len(consultation.pending_decisions), 1)
        decision = consultation.pending_decisions[0]
        self.assertEqual(decision["needed_from"], ["NUTRITION", "REHAB"])
        self.assertIn("月子餐", decision["question"])
        self.assertEqual(
            set(consultation.opinions.values_list("kind", flat=True)),
            {"RECOMMENDATION", "CONTRAINDICATION"},
        )

        # 会商未解决前，有权限的专业人员也不能恢复该项目。
        resp = self.client_for(self.nutritionist).post(
            f"/api/items/{self.meal.id}/resume/",
            {"base_revision": self.rev(), "note": "尝试恢复"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("会商", resp.data["detail"])

        # 服务主管组织会商，对每一项尚缺决定给出结论。
        resp = self.client_for(self.boss).post(
            f"/api/consultations/{consultation.id}/resolve/",
            {
                "base_revision": self.rev(),
                "decisions": [
                    {"key": decision["key"], "decision": "暂停高蛋白，改为低蛋白饮食"}
                ],
                "resolution": "按营养师禁忌意见调整，康复师同步修改训练配合方案",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, CarePlan.Status.ACTIVE)

        # 会商解决后，营养师撤销禁忌并确认恢复。
        contra = ProfessionalOpinion.objects.get(kind="CONTRAINDICATION")
        resp = self.client_for(self.nutritionist).post(
            f"/api/items/{self.meal.id}/resume/",
            {
                "base_revision": self.rev(),
                "note": "已更换低蛋白菜单，确认恢复配送",
                "supersede_opinion_ids": [contra.id],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.meal.refresh_from_db()
        self.assertEqual(self.meal.status, PlanItem.Status.SCHEDULED)
        contra.refresh_from_db()
        self.assertEqual(contra.status, ProfessionalOpinion.Status.SUPERSEDED)

    def test_resume_requires_authorized_professional(self):
        self.submit_opinion(
            self.nurse,
            kind="ASSESSMENT",
            content="伤口红肿，暂停护理",
            item_ids=[self.wound.id],
            pause_item_ids=[self.wound.id],
        )
        # 心理支持人员无权恢复护理项目。
        resp = self.client_for(self.psych).post(
            f"/api/items/{self.wound.id}/resume/",
            {"base_revision": self.rev(), "note": "越权尝试"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        # 产妇本人也不能恢复。
        resp = self.client_for(self.mother).post(
            f"/api/items/{self.wound.id}/resume/",
            {"base_revision": self.rev(), "note": "自己恢复"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        # 护理人员确认后恢复，并留下依据事件。
        resp = self.client_for(self.nurse).post(
            f"/api/items/{self.wound.id}/resume/",
            {"base_revision": self.rev(), "note": "红肿消退，恢复护理"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.wound.refresh_from_db()
        self.assertEqual(self.wound.status, PlanItem.Status.SCHEDULED)
        event = self.plan.events.filter(action="RESUMED", item=self.wound).latest("id")
        self.assertEqual(event.actor, self.nurse)
        self.assertIsNotNone(event.opinion)  # 恢复依据的确认意见

    def test_active_contraindication_blocks_resume_until_withdrawn(self):
        self.submit_opinion(
            self.nutritionist,
            kind="CONTRAINDICATION",
            content="忌食海鲜",
            item_ids=[self.meal.id],
        )
        # 禁忌仍有效，不能恢复。
        resp = self.client_for(self.nutritionist).post(
            f"/api/items/{self.meal.id}/resume/",
            {"base_revision": self.rev(), "note": "直接恢复"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("禁忌", resp.data["detail"])

    def test_completion_records_are_append_only(self):
        resp = self.client_for(self.nurse).post(
            f"/api/items/{self.wound.id}/complete/",
            {"base_revision": self.rev(), "note": "已完成上午伤口换药"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        record_id = resp.data["id"]

        # 后续计划调整不影响已产生的完成记录。
        self.submit_opinion(
            self.nurse, kind="FEEDBACK", content="下午伤口渗出偏多", item_ids=[self.wound.id]
        )
        resp = self.client_for(self.boss).get("/api/completions/")
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["id"], record_id)

        # API 不提供修改与删除。
        client = self.client_for(self.boss)
        self.assertEqual(client.put(f"/api/completions/{record_id}/", {}, format="json").status_code, 405)
        self.assertEqual(client.delete(f"/api/completions/{record_id}/").status_code, 405)

        # 模型层同样禁止修改与删除。
        from care.models import CompletionRecord
        from django.core.exceptions import ValidationError

        record = CompletionRecord.objects.get(id=record_id)
        record.note = "篡改"
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            record.delete()

    def test_supervisor_audit_view_traces_changes_and_conflicts(self):
        # 制造一次冲突并解决，然后检查主管追溯视图。
        self.submit_opinion(
            self.rehab, kind="RECOMMENDATION", content="高蛋白饮食", item_ids=[self.meal.id]
        )
        self.submit_opinion(
            self.nutritionist,
            kind="CONTRAINDICATION",
            content="禁忌高蛋白",
            item_ids=[self.meal.id],
        )
        consultation = Consultation.objects.get(plan=self.plan, status="OPEN")
        key = consultation.pending_decisions[0]["key"]
        self.client_for(self.boss).post(
            f"/api/consultations/{consultation.id}/resolve/",
            {
                "base_revision": self.rev(),
                "decisions": [{"key": key, "decision": "按禁忌执行"}],
                "resolution": "按营养师意见执行",
            },
            format="json",
        )

        resp = self.client_for(self.boss).get(f"/api/plans/{self.plan.id}/audit/")
        self.assertEqual(resp.status_code, 200)
        # 每次变更都有版本，版本号连续。
        numbers = [v["number"] for v in resp.data["versions"]]
        self.assertEqual(numbers, list(range(self.rev() + 1)))
        # 每次变更都有事件记录，含操作者与依据。
        self.assertGreaterEqual(len(resp.data["events"]), 4)
        self.assertTrue(all(e["actor"]["id"] for e in resp.data["events"]))
        # 会商已解决，不再出现在未解决冲突中。
        self.assertEqual(resp.data["open_consultations"], [])

        # 未解决冲突会出现在主管视图中。
        self.submit_opinion(
            self.rehab, kind="RECOMMENDATION", content="再次建议高蛋白", item_ids=[self.meal.id]
        )
        resp = self.client_for(self.boss).get(f"/api/plans/{self.plan.id}/audit/")
        self.assertEqual(len(resp.data["open_consultations"]), 1)
        self.assertEqual(
            resp.data["open_consultations"][0]["pending_decisions"][0]["needed_from"],
            ["NUTRITION", "REHAB"],
        )
