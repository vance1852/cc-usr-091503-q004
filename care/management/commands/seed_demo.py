"""创建演示用户与一份当日计划，便于手工体验 API。

用法：python manage.py seed_demo
"""

import datetime

from django.core.management.base import BaseCommand

from care import services
from care.models import Role, User


class Command(BaseCommand):
    help = "创建演示用户（各角色）与一份当日照护计划"

    def handle(self, *args, **options):
        users = {}
        for username, role in [
            ("mother1", Role.MOTHER),
            ("nurse1", Role.NURSE),
            ("nutrition1", Role.NUTRITIONIST),
            ("rehab1", Role.REHAB),
            ("psych1", Role.PSYCH),
            ("boss", Role.SUPERVISOR),
        ]:
            user, created = User.objects.get_or_create(username=username, defaults={"role": role})
            if created:
                user.set_password("demo1234")
                user.save()
            users[username] = user

        today = datetime.date.today()
        plan = users["mother1"].care_plans.filter(date=today).first()
        if plan is None:
            plan = services.create_plan(users["mother1"], today, users["boss"])
            services.add_item(
                plan, users["nurse1"], 0,
                discipline="NURSING", title="伤口护理", detail="每日两次",
            )
            services.add_item(
                plan, users["nutrition1"], 1,
                discipline="NUTRITION", title="月子餐配送", optional=True,
            )
            services.add_item(
                plan, users["rehab1"], 2,
                discipline="REHAB", title="盆底肌康复训练",
            )
            services.add_item(
                plan, users["psych1"], 3,
                discipline="PSYCH", title="心理疏导", optional=True,
            )
        self.stdout.write(self.style.SUCCESS(f"演示数据就绪：计划#{plan.id} {plan.date}"))
        self.stdout.write("用户：mother1/nurse1/nutrition1/rehab1/psych1/boss，密码均为 demo1234")
