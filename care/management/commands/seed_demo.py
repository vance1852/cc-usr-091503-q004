"""演示用：创建六个角色账号、令牌与一条当天计划。"""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token

from care import services
from care.models import ItemState, Plan, Profile, Role

User = get_user_model()

ACCOUNTS = [
    ("mama", Role.MOTHER, "产妇小林", "123456"),
    ("nurse", Role.NURSE, "护理师", "123456"),
    ("nutrition", Role.NUTRITIONIST, "营养师", "123456"),
    ("rehab", Role.REHABILITATOR, "康复师", "123456"),
    ("psych", Role.PSYCHOLOGIST, "心理支持师", "123456"),
    ("boss", Role.SUPERVISOR, "服务主管", "123456"),
]


class Command(BaseCommand):
    help = "创建演示账号（含 Token）与当天示例计划"

    def handle(self, *args, **options):
        users = {}
        for username, role, display, password in ACCOUNTS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": display},
            )
            user.set_password(password)
            user.save()
            if created or not hasattr(user, "profile"):
                Profile.objects.update_or_create(
                    user=user, defaults={"role": role, "display_name": display}
                )
            token, _ = Token.objects.get_or_create(user=user)
            users[role] = user
            self.stdout.write(f"{role:>13}  {username:>9} / {password}  "
                              f"Token {token.key}")

        plan, created_plan = Plan.objects.get_or_create(
            mother=users[Role.MOTHER], care_date=timezone.localdate(),
            defaults={"title": "产后第3日照护计划"},
        )
        if created_plan:
            rev = 1
            specs = [
                ("CARE-01", "会阴伤口换药", "care", users[Role.NURSE], time(9, 0), False),
                ("NUTR-01", "滋补月子餐", "nutrition", users[Role.NUTRITIONIST],
                 time(11, 30), False),
                ("REHAB-01", "盆底肌训练", "rehab", users[Role.REHABILITATOR],
                 time(15, 0), False),
                ("PSY-01", "情绪疏导（可选）", "psych", users[Role.PSYCHOLOGIST],
                 time(16, 0), True),
            ]
            for code, name, disc, actor, t, optional in specs:
                item = services.add_item(
                    actor=actor, plan=plan, code=code, name=name,
                    discipline=disc, scheduled_time=t, optional=optional,
                    expected_revision=rev,
                )
                rev += 1
                services.confirm_item(actor=actor, item=item,
                                      expected_revision=rev)
                rev += 1
            services.publish_version(actor=users[Role.NURSE], plan=plan,
                                     note="晨间基线版本", expected_revision=rev)
            self.stdout.write(self.style.SUCCESS(
                f"已创建示例计划 #{plan.id}（4 个已确认项目）"
            ))
        else:
            self.stdout.write(f"今天的计划已存在 #{plan.id}")
