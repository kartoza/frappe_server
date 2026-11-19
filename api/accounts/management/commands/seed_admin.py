"""Idempotently seed the single platform admin user from env vars."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Create (or update) the platform admin user from ADMIN_EMAIL/ADMIN_PASSWORD."""

    help = 'Seed the platform admin user from ADMIN_EMAIL/ADMIN_PASSWORD env vars.'

    def handle(self, *args, **options):
        """Run the command."""
        email = settings.PLATFORM_ADMIN_EMAIL
        password = settings.PLATFORM_ADMIN_PASSWORD

        if not password:
            self.stdout.write(self.style.WARNING(
                'ADMIN_PASSWORD is not set; skipping admin user seed.'
            ))
            return

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=email,
            defaults={'email': email, 'is_staff': True, 'is_superuser': True},
        )
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.email = email
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created admin user {email}.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Updated admin user {email}.'))
