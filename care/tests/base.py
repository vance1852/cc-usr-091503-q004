"""
测试辅助：构造六类账号与当天计划。
"""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from care import services
from care.models import Plan, PlanItem, Profile, Role

User = get_user_model()


class CareAPITestCase(APITestCase):
    def setUp(self):
        self.mother = self._user("mama", Role.MOTHER, "产妇小林")
        self.nurse = self._user("nurse", Role.NURSE, "护理师")
        self.nutritionist = self._user("nutrition", Role.NUTRITIONIST, "营养师")
        self.rehab = self._user("rehab", Role.REHABILITATOR, "康复师")
        self.psych = self._user("psych", Role.PSYCHOLOGIST, "心理支持师")
        self.supervisor = self._user("boss", Role.SUPERVISOR, "服务主管")
        self.other_mother = self._user("mama2", Role.MOTHER, "产妇小陈")

        self.plan = services.create_plan(
            actor=self.nurse, mother=self.mother,
            care_date=timezone.localdate(), title="产后第3日照护计划",
        )
        self.rev = 1

    def _user(self, username, role, display=""):
        user = User.objects.create_user(username=username, password="pw123456")
        Profile.objects.create(user=user, role=role, display_name=display)
        return user

    def login(self, user):
        self.client.force_authenticate(user)

    def add_item(self, code, name, discipline, *, actor=None, scheduled=None,
                 optional=False):
        item = services.add_item(
            actor=actor or self.nurse, plan=self.plan, code=code, name=name,
            discipline=discipline, scheduled_time=scheduled, optional=optional,
            expected_revision=self.rev,
        )
        self.rev += 1
        return item

    def confirm(self, item, actor=None):
        item = services.confirm_item(
            actor=actor or self._owner(item), item=item,
            expected_revision=self.rev,
        )
        self.rev += 1
        return item

    def _owner(self, item):
        return {
            "care": self.nurse,
            "nutrition": self.nutritionist,
            "rehab": self.rehab,
            "psych": self.psych,
        }[item.discipline]

    def pause(self, item, actor, reason="伤口不适"):
        services.pause_item(actor=actor, item=item, reason=reason,
                            expected_revision=self.rev)
        self.rev += 1
        item.refresh_from_db()
        return item

    def resume(self, item, actor, comment="重新评估后可以恢复"):
        services.resume_item(actor=actor, item=item, comment=comment,
                             expected_revision=self.rev)
        self.rev += 1
        item.refresh_from_db()
        return item
