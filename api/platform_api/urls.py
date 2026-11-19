"""Top-level URL routing for the platform API."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/', include('benches.urls')),
    path('api/', include('sites.urls')),
]
