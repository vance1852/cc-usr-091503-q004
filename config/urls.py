from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth-token/", obtain_auth_token, name="api-auth-token"),
    path("api/", include("care.urls")),
]
