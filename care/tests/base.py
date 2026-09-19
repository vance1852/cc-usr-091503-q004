"""测试共享基类：构造六个角色的用户与一份含四个专业项目的当日计划。"""

import datetime

from rest_framework.test import APIClient, APITestCase

from care import services
from care.models import CarePlan, Role, User


class CareApiTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mother = User.objects.create_user("mother1", password="x", role=Role.MOTHER)
        cls.mother2 = User.objects.create_user("mother2", password="x", role=Role.MOTHER)
        cls.nurse = User.objects.create_user("nurse1", password="x", role=Role.NURSE)
        cls.nutritionist = User.objects.create_user("nutrition1", password="x", role=Role.NUTRITIONIST)
        cls.rehab = User.objects.create_user("rehab1", password="x", role=Role.REHAB)
        cls.psych = User.objects.create_user("psych1", password="x", role=Role.PSYCH)
        cls.boss = User.objects.create_user("boss", password="x", role=Role.SUPERVISOR)

    def setUp(self):
        self.plan = services.create_plan(self.mother, datetime.date(2026, 9, 19), self.boss)
        self.wound = services.add_item(
            self.plan, self.nurse, self.rev(), discipline="NURSING", title="伤口护理"
        )
        self.meal = services.add_item(
            self.plan, self.nutritionist, self.rev(), discipline="NUTRITION",
            title="月子餐配送", optional=True,
        )
        self.exercise = services.add_item(
            self.plan, self.rehab, self.rev(), discipline="REHAB", title="盆底肌康复训练"
        )
        self.counseling = services.add_item(
            self.plan, self.psych, self.rev(), discipline="PSYCH",
            title="心理疏导", optional=True,
        )

    def rev(self):
        return CarePlan.objects.values_list("revision", flat=True).get(pk=self.plan.pk)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def submit_opinion(self, user, **payload):
        payload.setdefault("base_revision", self.rev())
        return self.client_for(user).post(
            f"/api/plans/{self.plan.id}/submit_opinion/", payload, format="json"
        )
