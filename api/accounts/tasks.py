"""Celery tasks for account-related background work."""
from celery import shared_task
from django.core.mail import send_mail

from .models import Invite


@shared_task
def send_invite_email(invite_id, invite_link):
    """Best-effort email delivery for an invite.

    Never fatal to the invite itself — the admin already has the link
    returned by the create-invite API call to share manually, so a
    missing/misconfigured SMTP setup (the default for a fresh deploy,
    which just logs to console) doesn't block inviting anyone.
    """
    try:
        invite = Invite.objects.get(pk=invite_id)
    except Invite.DoesNotExist:
        return
    try:
        send_mail(
            subject='You have been invited to the Frappe Platform',
            message=(
                "You've been invited to join the Frappe Platform.\n\n"
                f"Accept your invite here: {invite_link}\n\n"
                f"This link expires in {Invite.EXPIRY_DAYS} days."
            ),
            from_email=None,
            recipient_list=[invite.email],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001 — the invite link is the real delivery mechanism
        pass
