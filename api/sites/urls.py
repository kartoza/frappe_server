from rest_framework.routers import DefaultRouter

from .views import AppViewSet, BackupViewSet, JobViewSet, SiteViewSet

router = DefaultRouter()
router.register('apps', AppViewSet, basename='app')
router.register('sites', SiteViewSet, basename='site')
router.register('backups', BackupViewSet, basename='backup')
router.register('jobs', JobViewSet, basename='job')

urlpatterns = router.urls
