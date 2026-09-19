from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CarePlanViewSet,
    CompletionRecordViewSet,
    ConsultationViewSet,
    PlanItemViewSet,
    RefusalViewSet,
)

router = DefaultRouter()
router.register("plans", CarePlanViewSet, basename="plan")
router.register("items", PlanItemViewSet, basename="item")
router.register("refusals", RefusalViewSet, basename="refusal")
router.register("consultations", ConsultationViewSet, basename="consultation")
router.register("completions", CompletionRecordViewSet, basename="completion")

urlpatterns = [path("", include(router.urls))]
