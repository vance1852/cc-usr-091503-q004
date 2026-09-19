from django.contrib import admin

from .models import (
    ChangeLog,
    CompletionRecord,
    ConsultationIssue,
    PauseEvent,
    Plan,
    PlanItem,
    PlanVersion,
    ProfessionalOpinion,
    Profile,
    Refusal,
    ResumeEvent,
    VersionItem,
    VersionOpinion,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "display_name")
    list_filter = ("role",)


class PlanItemInline(admin.TabularInline):
    model = PlanItem
    extra = 0


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("title", "mother", "care_date", "revision", "current_version")
    list_filter = ("care_date",)
    inlines = [PlanItemInline]


@admin.register(ProfessionalOpinion)
class OpinionAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "item", "discipline", "opinion_type", "author",
                    "created_at")
    list_filter = ("discipline", "opinion_type")


@admin.register(PauseEvent)
class PauseAdmin(admin.ModelAdmin):
    list_display = ("item", "paused_by", "created_at", "cleared_at", "cleared_by")


@admin.register(ResumeEvent)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("item", "resumed_by", "created_at")


@admin.register(Refusal)
class RefusalAdmin(admin.ModelAdmin):
    list_display = ("item", "mother", "scope", "created_at")


@admin.register(CompletionRecord)
class CompletionAdmin(admin.ModelAdmin):
    list_display = ("item", "completed_by", "created_at")


@admin.register(ConsultationIssue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "item", "status", "decision", "created_at",
                    "decided_at")
    list_filter = ("status",)


class VersionItemInline(admin.TabularInline):
    model = VersionItem
    extra = 0


class VersionOpinionInline(admin.TabularInline):
    model = VersionOpinion
    extra = 0


@admin.register(PlanVersion)
class VersionAdmin(admin.ModelAdmin):
    list_display = ("plan", "number", "revision", "published_by", "created_at")
    inlines = [VersionItemInline, VersionOpinionInline]


@admin.register(ChangeLog)
class ChangeLogAdmin(admin.ModelAdmin):
    list_display = ("plan", "action", "actor", "item", "created_at")
    list_filter = ("action",)
