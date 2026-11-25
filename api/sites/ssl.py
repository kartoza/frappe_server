"""Issue and renew Let's Encrypt certificates for sites on real custom domains.

Runs certbot (installed as a plain pip dependency — no separate container
needed) directly inside this process via subprocess, using the HTTP-01
webroot method: nginx serves challenge files from a shared volume
(SSL_WEBROOT_DIR), certbot writes the issued cert into another shared
volume (SSL_CERT_DIR) that nginx also reads from (see
proxy/nginx.conf and sites/nginx_config.py).

Calling ``certbot certonly`` again for a domain that already has a
valid, not-soon-expiring certificate is a safe no-op — that's what makes
the exact same entry point usable for both first issuance and every
later renewal (see ``issue_or_renew`` and the periodic task that calls it
on a schedule in tasks.py).
"""
import logging
import subprocess

from django.conf import settings

logger = logging.getLogger(__name__)


def eligible_hostnames():
    """Every domain an active site answers to that could plausibly get a
    real certificate — its own hostname plus any custom domains (see
    SiteDomain), since each domain terminates its own TLS and so needs
    its own cert regardless of which one is that site's default.

    Excludes anything under settings.SSL_EXCLUDED_DOMAINS (just
    "localhost" by default) — those domains have no public DNS, so
    Let's Encrypt's HTTP-01 challenge could never reach this host for them.
    """
    from .models import Site

    all_domains = [
        domain
        for site in Site.objects.filter(status=Site.STATUS_ACTIVE).prefetch_related('domains')
        for domain in site.all_domains
    ]
    return [
        domain for domain in all_domains
        if not any(
            domain == excluded or domain.endswith(f'.{excluded}')
            for excluded in settings.SSL_EXCLUDED_DOMAINS
        )
    ]


def cert_path(hostname):
    """Path to a hostname's certificate chain, if one has been issued."""
    return f'{settings.SSL_CERT_DIR}/live/{hostname}/fullchain.pem'


def key_path(hostname):
    """Path to a hostname's private key, if one has been issued."""
    return f'{settings.SSL_CERT_DIR}/live/{hostname}/privkey.pem'


def has_cert(hostname):
    """Whether a certificate file exists on disk for this hostname.

    Used by nginx_config.py to decide whether to render an HTTPS server
    block — doesn't check expiry, since certbot (not nginx) is
    responsible for keeping the files on disk valid; nginx just needs to
    know whether to point at them at all.
    """
    import os

    return os.path.exists(cert_path(hostname))


def issue_or_renew(hostname):
    """Run certbot for one hostname. Returns (success, output).

    Idempotent: a no-op if the existing cert is still valid for more
    than 30 days (certbot's own default renewal window), otherwise
    issues a fresh one. Never raises — failures (DNS not pointed here
    yet, rate limits, port 80 unreachable, ...) are expected for a
    disabled/misconfigured domain and are reported back as a message
    rather than an exception, so one bad domain can't abort a batch run.
    """
    command = [
        'certbot', 'certonly', '--webroot',
        '-w', settings.SSL_WEBROOT_DIR,
        '-d', hostname,
        '--non-interactive', '--agree-tos',
        '-m', settings.SSL_CONTACT_EMAIL,
        '--config-dir', settings.SSL_CERT_DIR,
        '--work-dir', '/var/lib/letsencrypt',
        '--logs-dir', '/var/log/letsencrypt',
    ]
    if settings.SSL_STAGING:
        command.append('--staging')

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=120, check=False
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            logger.warning('certbot failed for %s: %s', hostname, output)
            return False, output
        return True, output
    except Exception as exc:  # noqa: BLE001 — one domain's failure must not abort the batch
        logger.warning('certbot errored for %s: %s', hostname, exc)
        return False, str(exc)


def run_for_all_eligible_sites():
    """Issue/renew certs for every eligible site. Returns {hostname: (success, output)}.

    Shared by the management command (manual/cron invocation) and the
    Celery beat task (automatic periodic renewal) so there's exactly one
    place this logic lives.
    """
    if not settings.SSL_ENABLED:
        return {}

    hostnames = eligible_hostnames()
    results = {hostname: issue_or_renew(hostname) for hostname in hostnames}

    if hostnames:
        # Reload regardless of whether certbot actually changed anything
        # this run (it no-ops successfully when a cert is still fresh) —
        # a reload is cheap and graceful, and this is the only place that
        # guarantees nginx picks up a cert the moment it's first issued.
        from . import nginx_config
        nginx_config.regenerate_and_reload()

    return results
