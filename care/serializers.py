from rest_framework import serializers

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


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "role", "role_display"]


class PlanItemSerializer(serializers.ModelSerializer):
    discipline_display = serializers.CharField(source="get_discipline_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = PlanItem
        fields = [
            "id", "plan", "discipline", "discipline_display", "title", "detail",
            "optional", "status", "status_display", "pause_reason", "paused_at",
            "created_at",
        ]
        read_only_fields = ["plan", "status", "pause_reason", "paused_at", "created_at"]


class OpinionSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    discipline_display = serializers.CharField(source="get_discipline_display", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ProfessionalOpinion
        fields = [
            "id", "plan", "author", "discipline", "discipline_display", "kind",
            "kind_display", "content", "items", "status", "status_display",
            "supersedes", "created_at",
        ]
        read_only_fields = ["plan", "author", "discipline", "status", "created_at"]


class PlanVersionSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    opinions = OpinionSerializer(many=True, read_only=True)

    class Meta:
        model = PlanVersion
        fields = ["id", "number", "reason", "created_by", "opinions", "items_snapshot", "created_at"]


class PlanVersionBriefSerializer(serializers.ModelSerializer):
    """版本列表用：只带意见 id，避免嵌套过深。"""

    created_by = UserSerializer(read_only=True)
    opinion_ids = serializers.PrimaryKeyRelatedField(
        source="opinions", many=True, read_only=True
    )

    class Meta:
        model = PlanVersion
        fields = ["id", "number", "reason", "created_by", "opinion_ids", "items_snapshot", "created_at"]


class CarePlanSerializer(serializers.ModelSerializer):
    mother = UserSerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    items = PlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = CarePlan
        fields = ["id", "mother", "date", "status", "status_display", "revision", "items", "created_at"]


class CompletionRecordSerializer(serializers.ModelSerializer):
    performed_by = UserSerializer(read_only=True)

    class Meta:
        model = CompletionRecord
        fields = ["id", "plan", "item", "performed_by", "note", "created_at"]


class ItemEventSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    opinion_id = serializers.IntegerField(source="opinion.id", read_only=True, default=None)

    class Meta:
        model = ItemEvent
        fields = ["id", "item", "actor", "action", "action_display", "reason", "opinion_id", "created_at"]


class RefusalSerializer(serializers.ModelSerializer):
    mother = UserSerializer(read_only=True)
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Refusal
        fields = [
            "id", "plan", "item", "mother", "scope", "scope_display", "reason",
            "is_active", "created_at", "revoked_at", "revoked_by",
        ]
        read_only_fields = ["plan", "mother", "created_at", "revoked_at", "revoked_by"]


class ConsultationSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    opinions = OpinionSerializer(many=True, read_only=True)
    resolved_by = UserSerializer(read_only=True)

    class Meta:
        model = Consultation
        fields = [
            "id", "plan", "status", "status_display", "summary", "opinions",
            "pending_decisions", "decisions", "resolution", "resolved_by",
            "created_at", "resolved_at",
        ]
