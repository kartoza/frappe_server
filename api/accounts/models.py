"""Models for inviting new users onto the platform."""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def _generate_token():
    return secrets.token_urlsafe(32)


class Invite(models.Model):
    """A pending invitation for someone to join the platform.

    One row per email — re-inviting an address (see AccountsViewSet logic)
    just refreshes this same row's token/expiry rather than creating a
    second one, so there's never ambiguity about which link is current.
    """

    EXPIRY_DAYS = 7

    email = models.EmailField(unique=True)
    token = models.CharField(max_length=64, unique=True, default=_generate_token)
    is_admin = models.BooleanField(default=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='sent_invites'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email

    @property
    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(days=self.EXPIRY_DAYS)

    @property
    def is_accepted(self):
        return self.accepted_at is not None

    @property
    def status(self):
        if self.is_accepted:
            return 'accepted'
        if self.is_expired:
            return 'expired'
        return 'pending'

    def refresh(self, is_admin, invited_by):
        """Reset an existing (unaccepted) invite to a fresh token/expiry — used for re-invites."""
        self.token = _generate_token()
        self.is_admin = is_admin
        self.invited_by = invited_by
        self.accepted_at = None
        self.created_at = timezone.now()
        self.save(update_fields=['token', 'is_admin', 'invited_by', 'accepted_at', 'created_at'])
