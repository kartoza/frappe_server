from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AppViewSet,
    BackupViewSet,
    JobViewSet,
    SiteViewSet,
    SqlDatabasesView,
    SqlQueryView,
)

router = DefaultRouter()
router.register('apps', AppViewSet, basename='app')
router.register('sites', SiteViewSet, basename='site')
router.register('backups', BackupViewSet, basename='backup')
router.register('jobs', JobViewSet, basename='job')

urlpatterns = router.urls + [
    path('sql/databases/', SqlDatabasesView.as_view(), name='sql-databases'),
    path('sql/query/', SqlQueryView.as_view(), name='sql-query'),
]
