"""Authentication, invite, and user-management views.

Login itself is handled by djangorestframework-simplejwt's built-in views
(wired up in urls.py); everything here is the extra bits DRF doesn't
provide out of the box: the current user's own profile/password, and
admin-only invite + user management.
"""
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Invite
from .serializers import (
    AcceptInviteSerializer,
    ChangePasswordSerializer,
    CreateInviteSerializer,
    InviteSerializer,
    UserSerializer,
)
from .tasks import send_invite_email

User = get_user_model()


class MeView(APIView):
    """Get or update the currently authenticated user's own profile."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_admin': user.is_staff,
        })

    def patch(self, request):
        """Update display name fields. Email/username are fixed — they're the login identity."""
        user = request.user
        for field in ('first_name', 'last_name'):
            if field in request.data:
                setattr(user, field, request.data[field])
        user.save(update_fields=['first_name', 'last_name'])
        return self.get(request)


class ChangePasswordView(APIView):
    """Let the current user change their own password, given the current one."""

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data['current_password']):
            return Response(
                {'detail': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST
            )
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        return Response({'detail': 'Password updated.'})


class UserViewSet(
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Admin-only management of other users (promote/demote, deactivate, remove).

    No create action here — accounts only come into existence by accepting
    an invite (see InviteViewSet), so there's always a record of who
    invited whom.
    """

    queryset = User.objects.all().order_by('username')
    serializer_class = UserSerializer
    permission_classes = (IsAdminUser,)

    # Only admins can reach these actions (IsAdminUser, above), and a given
    # admin always survives any change they make to *someone else* — so
    # blocking self-demotion/self-deletion here is sufficient on its own to
    # guarantee at least one admin always remains; no separate "last admin"
    # headcount check is reachable on top of it.
    def update(self, request, *args, **kwargs):
        target = self.get_object()
        serializer = self.get_serializer(target, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)

        # Checked against validated_data, not raw request.data — a
        # multipart-encoded `is_staff=False` arrives as the *string*
        # "False", which is never `is False` by identity; routing it
        # through the serializer's BooleanField first coerces it properly
        # regardless of encoding.
        losing_admin = serializer.validated_data.get('is_staff') is False and target.is_staff
        losing_active = serializer.validated_data.get('is_active') is False and target.is_active
        if (losing_admin or losing_active) and target == request.user:
            return Response(
                {'detail': 'You cannot remove your own admin access or deactivate yourself.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        if target == request.user:
            return Response(
                {'detail': 'You cannot delete your own account.'}, status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)


class InviteViewSet(
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Admin-only: invite new users and manage pending invites."""

    queryset = Invite.objects.all().order_by('-created_at')
    serializer_class = InviteSerializer
    permission_classes = (IsAdminUser,)

    def create(self, request, *args, **kwargs):
        """Create a new invite, or refresh an existing unaccepted one for the same email.

        An already-*accepted* invite means a user exists for that email —
        CreateInviteSerializer's own uniqueness check catches that case
        (every accepted invite has a matching User row), so there's no
        separate check needed here.
        """
        serializer = CreateInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        is_admin = serializer.validated_data['is_admin']

        invite, created = Invite.objects.get_or_create(
            email=email, defaults={'is_admin': is_admin, 'invited_by': request.user}
        )
        if not created:
            invite.refresh(is_admin=is_admin, invited_by=request.user)

        invite_link = request.build_absolute_uri(f'/accept-invite/{invite.token}')
        send_invite_email.delay(invite.id, invite_link)

        return Response(
            {**InviteSerializer(invite).data, 'invite_link': invite_link},
            status=status.HTTP_201_CREATED,
        )


class InvitePublicViewSet(viewsets.ViewSet):
    """Unauthenticated endpoints for the invite-acceptance page itself."""

    permission_classes = (AllowAny,)

    def retrieve(self, request, token=None):
        """Validate a token and return just enough to render the accept-invite form."""
        invite = get_object_or_404(Invite, token=token)
        if invite.status != 'pending':
            return Response(
                {'detail': f'This invite is {invite.status}.'}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({'email': invite.email, 'is_admin': invite.is_admin})

    @action(detail=True, methods=['post'])
    def accept(self, request, token=None):
        """Create the account from a pending invite and log the new user in."""
        invite = get_object_or_404(Invite, token=token)
        if invite.status != 'pending':
            return Response(
                {'detail': f'This invite is {invite.status}.'}, status=status.HTTP_400_BAD_REQUEST
            )

        serializer = AcceptInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.create_user(
            username=invite.email,
            email=invite.email,
            password=serializer.validated_data['password'],
            is_staff=invite.is_admin,
            is_superuser=invite.is_admin,
        )
        invite.accepted_at = timezone.now()
        invite.save(update_fields=['accepted_at'])

        refresh = RefreshToken.for_user(user)
        return Response(
            {'access': str(refresh.access_token), 'refresh': str(refresh)},
            status=status.HTTP_201_CREATED,
        )
