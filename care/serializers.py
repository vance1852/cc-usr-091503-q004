from rest_framework import serializers

from .models import (
    ChangeLog,
    CompletionRecord,
    ConsultationIssue,
    PauseEvent,
    Plan,
    PlanItem,
    PlanVersion,
    ProfessionalOpinion,
    Refusal,
    ResumeEvent,
    Role,
    VersionItem,
    VersionOpinion,
)


class OpinionSerializer(serializers.ModelSerializer):
    author = serializers.ReadOnlyField(source="author.username")
    author_role = serializers.ReadOnlyField(source="author.profile.get_role_display")

    class Meta:
        model = ProfessionalOpinion
        fields = ["id", "item", "author", "author_role", "discipline",
                  "opinion_type", "content", "created_at"]
        read_only_fields = ["id", "author", "author_role", "discipline", "created_at"]


class RefusalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refusal
        fields = ["id", "item", "mother", "scope", "reason", "created_at"]
        read_only_fields = ["id", "mother", "created_at"]


class ItemSerializer(serializers.ModelSerializer):
    refusal = RefusalSerializer(read_only=True)
    pause_reason = serializers.SerializerMethodField()

    class Meta:
        model = PlanItem
        fields = ["id", "code", "name", "discipline", "scheduled_time",
                  "optional", "state", "refusal", "pause_reason", "created_at"]
        read_only_fields = ["id", "state", "created_at"]

    def get_pause_reason(self, obj):
        last = obj.pause_events.order_by("-created_at", "-id").first()
        if last and last.cleared_at is None:
            return {"reason": last.reason_text, "at": last.created_at,
                    "paused_by": last.paused_by.username}
        return None


class PlanSerializer(serializers.ModelSerializer):
    mother = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Plan
        fields = ["id", "mother", "care_date", "title", "revision",
                  "current_version", "created_at"]
        read_only_fields = ["id", "revision", "current_version", "created_at"]


class PlanCreateSerializer(serializers.Serializer):
    mother_username = serializers.CharField()
    care_date = serializers.DateField()
    title = serializers.CharField(max_length=128)


class ItemCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=128)
    discipline = serializers.ChoiceField(choices=("care", "nutrition", "rehab", "psych"))
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
    optional = serializers.BooleanField(default=False)
    expected_revision = serializers.IntegerField()


class OpinionCreateSerializer(serializers.Serializer):
    opinion_type = serializers.ChoiceField(
        choices=["ASSESSMENT", "RECOMMENDATION", "CONTRAINDICATION", "FEEDBACK"]
    )
    content = serializers.CharField()
    item = serializers.PrimaryKeyRelatedField(
        queryset=PlanItem.objects.all(), required=False, allow_null=True
    )
    expected_revision = serializers.IntegerField()


class PauseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
    expected_revision = serializers.IntegerField()


class ResumeSerializer(serializers.Serializer):
    comment = serializers.CharField(max_length=255)
    expected_revision = serializers.IntegerField()


class RefusalCreateSerializer(serializers.Serializer):
    scope = serializers.CharField(max_length=255, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
    expected_revision = serializers.IntegerField()


class RevisionSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField()


class CompleteSerializer(serializers.Serializer):
    comment = serializers.CharField(max_length=255, required=False, allow_blank=True)
    expected_revision = serializers.IntegerField()


class PublishSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)
    expected_revision = serializers.IntegerField()


class ResolveSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["PROCEED", "HALT"])
    note = serializers.CharField(max_length=255)
    expected_revision = serializers.IntegerField()


class CompletionSerializer(serializers.ModelSerializer):
    completed_by = serializers.ReadOnlyField(source="completed_by.username")

    class Meta:
        model = CompletionRecord
        fields = ["id", "completed_by", "comment", "plan_version", "created_at"]


class IssueSerializer(serializers.ModelSerializer):
    item_code = serializers.ReadOnlyField(source="item.code")
    item_name = serializers.ReadOnlyField(source="item.name")

    class Meta:
        model = ConsultationIssue
        fields = ["id", "item", "item_code", "item_name", "missing_decision",
                  "status", "decision", "resolution_note", "decided_at", "created_at"]
        read_only_fields = fields


class VersionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersionItem
        fields = ["code", "name", "discipline", "scheduled_time", "optional", "state"]


class VersionOpinionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersionOpinion
        fields = ["author_name", "discipline", "opinion_type", "content", "created_at"]


class VersionSerializer(serializers.ModelSerializer):
    items = VersionItemSerializer(many=True, read_only=True)
    opinion_bindings = VersionOpinionSerializer(many=True, read_only=True)
    published_by = serializers.ReadOnlyField(source="published_by.username")

    class Meta:
        model = PlanVersion
        fields = ["id", "number", "revision", "note", "published_by", "created_at",
                  "items", "opinion_bindings"]


class ChangeLogSerializer(serializers.ModelSerializer):
    actor = serializers.ReadOnlyField(source="actor.username")
    action_display = serializers.ReadOnlyField(source="get_action_display")

    class Meta:
        model = ChangeLog
        fields = ["id", "action", "action_display", "actor", "item", "opinion",
                  "detail", "created_at"]


class RoleAssignSerializer(serializers.Serializer):
    username = serializers.CharField()
    role = serializers.ChoiceField(choices=[r[0] for r in Role.choices])
    display_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
