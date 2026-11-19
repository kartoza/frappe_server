from rest_framework.routers import DefaultRouter

from .views import BenchViewSet

router = DefaultRouter()
router.register('benches', BenchViewSet, basename='bench')

urlpatterns = router.urls
