from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    CarePlan,
    CompletionRecord,
    Consultation,
    ItemEvent,
    PlanItem,
    PlanVersion,
    ProfessionalOpinion,
    Refusal,
    User,
)

admin.site.register(User, UserAdmin)
admin.site.register(
    [CarePlan, PlanItem, PlanVersion, ProfessionalOpinion, CompletionRecord, ItemEvent, Refusal, Consultation]
)
