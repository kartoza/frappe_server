"""Tests for authentication, invites, user management, and profile endpoints."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Invite

User = get_user_model()


class LoginTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin@example.com',
            email='admin@example.com',
            password='s3cret-pass',
            is_staff=True,
            is_superuser=True,
        )

    def test_login_with_valid_credentials_returns_tokens(self):
        response = self.client.post(
            reverse('login'),
            {'username': 'admin@example.com', 'password': 's3cret-pass'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_login_with_invalid_credentials_is_rejected(self):
        response = self.client.post(
            reverse('login'),
            {'username': 'admin@example.com', 'password': 'wrong'},
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        response = self.client.get(reverse('me'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_current_user_once_authenticated(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'admin@example.com')
        self.assertTrue(response.data['is_admin'])


class ProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='member@example.com', email='member@example.com', password='s3cret-pass',
        )
        self.client.force_authenticate(user=self.user)

    def test_patch_me_updates_display_name_only(self):
        response = self.client.patch(
            reverse('me'), {'first_name': 'Ada', 'last_name': 'Lovelace', 'email': 'hacker@example.com'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Ada')
        self.assertEqual(self.user.last_name, 'Lovelace')
        self.assertEqual(self.user.email, 'member@example.com')  # untouched — not an editable field

    def test_change_password_requires_correct_current_password(self):
        response = self.client.post(
            reverse('change-password'),
            {'current_password': 'wrong', 'new_password': 'brand-new-pass-123'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('s3cret-pass'))

    def test_change_password_succeeds_with_correct_current_password(self):
        response = self.client.post(
            reverse('change-password'),
            {'current_password': 's3cret-pass', 'new_password': 'brand-new-pass-123'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-pass-123'))

    def test_change_password_rejects_weak_new_password(self):
        response = self.client.post(
            reverse('change-password'),
            {'current_password': 's3cret-pass', 'new_password': '123'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class InviteManagementTests(APITestCase):
    """Only admins can invite/list/revoke invites."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin@example.com', email='admin@example.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        self.member = User.objects.create_user(
            username='member@example.com', email='member@example.com', password='pw',
        )

    @patch('accounts.views.send_invite_email.delay')
    def test_non_admin_cannot_create_invite(self, mock_email):
        self.client.force_authenticate(user=self.member)
        response = self.client.post('/api/auth/invites/', {'email': 'new@example.com'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_email.assert_not_called()

    @patch('accounts.views.send_invite_email.delay')
    def test_admin_can_create_invite_and_gets_link_back(self, mock_email):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/auth/invites/', {'email': 'new@example.com', 'is_admin': False})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('invite_link', response.data)
        self.assertTrue(Invite.objects.filter(email='new@example.com').exists())
        mock_email.assert_called_once()

    @patch('accounts.views.send_invite_email.delay')
    def test_creating_invite_for_an_existing_user_email_is_rejected(self, mock_email):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/auth/invites/', {'email': 'member@example.com'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_email.assert_not_called()

    @patch('accounts.views.send_invite_email.delay')
    def test_reinviting_the_same_pending_email_refreshes_the_token(self, mock_email):
        self.client.force_authenticate(user=self.admin)
        first = self.client.post('/api/auth/invites/', {'email': 'new@example.com'})
        second = self.client.post('/api/auth/invites/', {'email': 'new@example.com', 'is_admin': True})

        self.assertEqual(Invite.objects.filter(email='new@example.com').count(), 1)
        self.assertNotEqual(first.data['token'], second.data['token'])
        invite = Invite.objects.get(email='new@example.com')
        self.assertTrue(invite.is_admin)

    def test_admin_can_list_and_revoke_invites(self):
        invite = Invite.objects.create(email='new@example.com', invited_by=self.admin)
        self.client.force_authenticate(user=self.admin)

        list_response = self.client.get('/api/auth/invites/')
        self.assertEqual(len(list_response.data), 1)

        delete_response = self.client.delete(f'/api/auth/invites/{invite.id}/')
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Invite.objects.filter(id=invite.id).exists())


class AcceptInviteTests(APITestCase):
    """The public (unauthenticated) side of accepting an invite."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin@example.com', email='admin@example.com', password='pw', is_staff=True,
        )
        self.invite = Invite.objects.create(email='new@example.com', invited_by=self.admin)

    def test_retrieve_valid_invite_returns_email(self):
        response = self.client.get(f'/api/auth/invite/{self.invite.token}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'new@example.com')

    def test_retrieve_unknown_token_is_404(self):
        response = self.client.get('/api/auth/invite/does-not-exist/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_already_accepted_invite_is_rejected(self):
        from django.utils import timezone
        self.invite.accepted_at = timezone.now()
        self.invite.save(update_fields=['accepted_at'])

        response = self.client.get(f'/api/auth/invite/{self.invite.token}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_expired_invite_is_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        self.invite.created_at = timezone.now() - timedelta(days=Invite.EXPIRY_DAYS + 1)
        self.invite.save(update_fields=['created_at'])

        response = self.client.get(f'/api/auth/invite/{self.invite.token}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_creates_user_and_returns_tokens(self):
        response = self.client.post(
            f'/api/auth/invite/{self.invite.token}/accept/', {'password': 'brand-new-pass-123'}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

        user = User.objects.get(username='new@example.com')
        self.assertTrue(user.check_password('brand-new-pass-123'))
        self.assertFalse(user.is_staff)

        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, 'accepted')

    def test_accept_grants_admin_when_invite_was_for_an_admin(self):
        self.invite.is_admin = True
        self.invite.save(update_fields=['is_admin'])

        self.client.post(f'/api/auth/invite/{self.invite.token}/accept/', {'password': 'brand-new-pass-123'})
        user = User.objects.get(username='new@example.com')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_accept_twice_fails_the_second_time(self):
        self.client.post(f'/api/auth/invite/{self.invite.token}/accept/', {'password': 'brand-new-pass-123'})
        second = self.client.post(f'/api/auth/invite/{self.invite.token}/accept/', {'password': 'another-pass-456'})
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_rejects_weak_password(self):
        response = self.client.post(f'/api/auth/invite/{self.invite.token}/accept/', {'password': '123'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserManagementTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin@example.com', email='admin@example.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        self.other_admin = User.objects.create_user(
            username='other-admin@example.com', email='other-admin@example.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        self.member = User.objects.create_user(
            username='member@example.com', email='member@example.com', password='pw',
        )
        self.client.force_authenticate(user=self.admin)

    def test_non_admin_cannot_list_users(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_list_users(self):
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)

    def test_admin_can_promote_a_member(self):
        response = self.client.patch(f'/api/auth/users/{self.member.id}/', {'is_staff': True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_staff)

    def test_admin_can_deactivate_a_member(self):
        response = self.client.patch(f'/api/auth/users/{self.member.id}/', {'is_active': False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

    def test_admin_cannot_demote_themselves(self):
        response = self.client.patch(f'/api/auth/users/{self.admin.id}/', {'is_staff': False})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_admin_cannot_deactivate_themselves(self):
        response = self.client.patch(f'/api/auth/users/{self.admin.id}/', {'is_active': False})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_demote_a_different_admin(self):
        response = self.client.patch(f'/api/auth/users/{self.other_admin.id}/', {'is_staff': False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other_admin.refresh_from_db()
        self.assertFalse(self.other_admin.is_staff)

    def test_admin_can_delete_a_member(self):
        response = self.client.delete(f'/api/auth/users/{self.member.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.member.id).exists())

    def test_admin_cannot_delete_themselves(self):
        response = self.client.delete(f'/api/auth/users/{self.admin.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(id=self.admin.id).exists())
