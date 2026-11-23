"""Issue/renew Let's Encrypt certs for every eligible site.

Shares its logic with the periodic Celery task (see tasks.py and
platform_api/celery.py's beat schedule) — this command exists for manual
runs and for operators who'd rather drive renewal from host cron than
rely on Celery beat.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from sites.ssl import run_for_all_eligible_sites


class Command(BaseCommand):
    help = "Issue/renew Let's Encrypt certificates for every active site on a real domain."

    def handle(self, *args, **options):
        if not settings.SSL_ENABLED:
            self.stdout.write(self.style.WARNING('SSL_ENABLED is not set — skipping.'))
            return

        results = run_for_all_eligible_sites()
        if not results:
            self.stdout.write('No eligible sites (nothing outside SSL_EXCLUDED_DOMAINS).')
            return

        for hostname, (success, output) in results.items():
            if success:
                self.stdout.write(self.style.SUCCESS(f'{hostname}: ok'))
            else:
                self.stdout.write(self.style.ERROR(f'{hostname}: failed\n{output}'))
