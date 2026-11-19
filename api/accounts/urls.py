from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import ChangePasswordView, InvitePublicViewSet, InviteViewSet, MeView, UserViewSet

router = DefaultRouter()
router.register('invites', InviteViewSet, basename='invite')
router.register('users', UserViewSet, basename='user')

urlpatterns = [
    path('login/', TokenObtainPairView.as_view(), name='login'),
    path('refresh/', TokenRefreshView.as_view(), name='refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),
    # Singular "invite/" (public, token-based) vs. the admin-only plural
    # "invites/" router above — kept on separate prefixes so there's no
    # ambiguity between a numeric pk and a random token in the URL.
    path('invite/<str:token>/', InvitePublicViewSet.as_view({'get': 'retrieve'}), name='invite-detail'),
    path('invite/<str:token>/accept/', InvitePublicViewSet.as_view({'post': 'accept'}), name='invite-accept'),
    path('', include(router.urls)),
]
