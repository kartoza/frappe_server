"""Override Django's createsuperuser to follow this platform's login convention.

Every account on the platform logs in with its email as the username (see
seed_admin and accounts.views.InvitePublicViewSet.accept, which both set
username=email directly) rather than a separate username chosen at account
creation time. Django's stock createsuperuser prompts for "Username" and
"Email address" as two independent fields, which would silently break login
for any admin created this way whose answers to those two prompts don't
happen to match. This replaces that command with one that only ever asks
for an email and a password, so there's no separate username to get wrong.

Picked up instead of django.contrib.auth's version of the same command
because 'accounts' is listed after 'django.contrib.auth' in INSTALLED_APPS.
"""
import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    """Create a platform admin user, using their email as both username and login."""

    help = 'Create a platform admin user. Login is always by email.'

    def add_arguments(self, parser):
        parser.add_argument('--email', help='Email address for the new admin.')
        parser.add_argument(
            '--noinput', '--no-input', action='store_false', dest='interactive',
            help=(
                'Tells Django to NOT prompt the user for input of any kind. '
                'You must use --email, and provide a password via the '
                'DJANGO_SUPERUSER_PASSWORD environment variable (an admin '
                'created without one will not be able to log in until given '
                'a valid password).'
            ),
        )

    def handle(self, *args, **options):
        """Run the command."""
        user_model = get_user_model()
        email = options.get('email') or os.environ.get('DJANGO_SUPERUSER_EMAIL')

        if options['interactive']:
            while not email:
                email = input('Email address: ').strip()
                try:
                    validate_email(email)
                except ValidationError as exc:
                    self.stderr.write('; '.join(exc.messages))
                    email = None
                    continue
                if user_model.objects.filter(username=email).exists():
                    self.stderr.write(f'Error: {email} is already taken.')
                    email = None

            password = None
            while password is None:
                raw = getpass.getpass()
                confirm = getpass.getpass('Password (again): ')
                if raw != confirm:
                    self.stderr.write("Error: Your passwords didn't match.")
                    continue
                if not raw.strip():
                    self.stderr.write("Error: Blank passwords aren't allowed.")
                    continue
                try:
                    validate_password(raw, user_model(username=email, email=email))
                except ValidationError as exc:
                    self.stderr.write('\n'.join(exc.messages))
                    if input('Bypass password validation and create user anyway? [y/N]: ').lower() != 'y':
                        continue
                password = raw
        else:
            if not email:
                raise CommandError('You must use --email with --noinput.')
            try:
                validate_email(email)
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages))
            if user_model.objects.filter(username=email).exists():
                raise CommandError(f'{email} is already taken.')
            # Left unset: a superuser created without one gets an unusable
            # password until given a real one, same as Django's own command.
            password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

        user_model.objects.create_superuser(username=email, email=email, password=password)
        if options['verbosity'] >= 1:
            self.stdout.write(self.style.SUCCESS(f'Superuser {email} created successfully.'))
