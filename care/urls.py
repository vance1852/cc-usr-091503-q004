from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from .views import (
    IssueResolveView,
    MotherTodayView,
    OpenIssuesView,
    PlanViewSet,
    RoleAssignView,
)
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r"plans", PlanViewSet, basename="plan")

urlpatterns = [
    path("auth/login/", obtain_auth_token),
    path("mother/today/", MotherTodayView.as_view()),
    path("supervisor/issues/open/", OpenIssuesView.as_view()),
    path("issues/<int:issue_id>/resolve/", IssueResolveView.as_view()),
    path("admin/roles/", RoleAssignView.as_view()),
    path("", include(router.urls)),
]
