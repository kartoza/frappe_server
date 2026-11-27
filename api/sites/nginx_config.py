"""Generate and reload the proxy's per-site nginx configuration.

Because sites on different benches share no naming convention that would
let nginx derive "which bench" from a hostname alone, one explicit
server block per active site is generated and written to a file the proxy
container has mounted, then nginx is told to reload.

A site can answer to more than one domain (its own fixed hostname plus
any number of custom domains — see SiteDomain): exactly one of those is
its "default" domain, which gets the real proxy block; every other
domain the site answers to just 301-redirects there. Whichever domain
actually proxies sets the X-Frappe-Site-Name header to the site's real
(immutable) hostname, since Frappe resolves which site to serve from
that header first, falling back to the Host header only if it's absent —
without it, a request arriving on a custom domain would resolve to a
site folder matching that domain, which doesn't exist.

Every domain's HTTP (port 80) block always serves the Let's Encrypt
HTTP-01 challenge path itself, regardless of whether SSL is enabled or
this particular domain has a certificate yet — needed for the very
first issuance (see sites.ssl) and for every later renewal once the
domain has its own server block instead of falling through to the
platform's default one.
"""
import os

import redis
from django.conf import settings

from benches.container_exec import run_command

LOCK_KEY = 'nginx-config-regen-lock'
LOCK_TIMEOUT = 30

ACME_CHALLENGE_LOCATION = """\
    location /.well-known/acme-challenge/ {{
        root {webroot_dir};
    }}
"""

# Frappe's own static-file middleware for /files/ (serving a site's public
# uploads) ignores X-Frappe-Site-Name entirely and derives the site folder
# straight from the raw Host header instead (see frappe/middlewares.py's
# StaticDataMiddleware) - unlike every dynamic route, which checks that
# header first. So a request for /files/... arriving on anything other than
# the site's own immutable hostname would 404 looking for a site folder
# matching the custom domain, which never exists. Overriding Host here is
# the only way to make this one path resolve correctly too.
FILES_LOCATION = """\
    location /files/ {{
        set $bench_web http://{bench_container}:8000;
        proxy_pass $bench_web;
        proxy_set_header Host {site_hostname};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Frappe-Site-Name {site_hostname};
        proxy_read_timeout 300;
    }}
"""

PROXY_LOCATIONS = """\
    location /socket.io/ {{
        set $bench_socketio http://{bench_container}:9000;
        proxy_pass $bench_socketio;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Frappe-Site-Name {site_hostname};
    }}

""" + FILES_LOCATION + """\

    location / {{
        set $bench_web http://{bench_container}:8000;
        proxy_pass $bench_web;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Frappe-Site-Name {site_hostname};
        proxy_read_timeout 300;
    }}
"""

# Used both for the same-domain HTTP->HTTPS upgrade (scheme="https",
# target="$host" — a literal string, not python interpolation, so it's
# nginx's own variable in the rendered file) and for a non-default
# domain redirecting to wherever the default domain actually is.
REDIRECT_LOCATION = """\
    location / {{
        return 301 {scheme}://{target}$request_uri;
    }}
"""

HTTP_TEMPLATE = """\
server {{
    listen 80;
    server_name {hostname};

""" + ACME_CHALLENGE_LOCATION + """\
{body}}}
"""

HTTPS_TEMPLATE = """\
server {{
    listen 443 ssl;
    server_name {hostname};

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};

{body}}}
"""


def _redis_client():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


def _blocks_for_domain(domain, *, is_default, default_domain, site_hostname, bench_container):
    from . import ssl as ssl_module

    has_own_cert = settings.SSL_ENABLED and ssl_module.has_cert(domain)
    default_has_cert = settings.SSL_ENABLED and ssl_module.has_cert(default_domain)
    # A non-default domain without its own cert yet can't terminate TLS at
    # all, so it has to send plain-HTTP visitors straight to wherever the
    # default domain currently lives rather than upgrading itself first.
    redirect_to_default_scheme = 'https' if default_has_cert else 'http'

    if is_default:
        proxy_body = PROXY_LOCATIONS.format(bench_container=bench_container, site_hostname=site_hostname)
        if not has_own_cert:
            return [HTTP_TEMPLATE.format(hostname=domain, webroot_dir=settings.SSL_WEBROOT_DIR, body=proxy_body)]
        http_body = REDIRECT_LOCATION.format(scheme='https', target='$host')
        return [
            HTTP_TEMPLATE.format(hostname=domain, webroot_dir=settings.SSL_WEBROOT_DIR, body=http_body),
            HTTPS_TEMPLATE.format(
                hostname=domain, cert_path=ssl_module.cert_path(domain), key_path=ssl_module.key_path(domain),
                body=proxy_body,
            ),
        ]

    if not has_own_cert:
        body = REDIRECT_LOCATION.format(scheme=redirect_to_default_scheme, target=default_domain)
        return [HTTP_TEMPLATE.format(hostname=domain, webroot_dir=settings.SSL_WEBROOT_DIR, body=body)]
    http_body = REDIRECT_LOCATION.format(scheme='https', target='$host')
    https_body = REDIRECT_LOCATION.format(scheme=redirect_to_default_scheme, target=default_domain)
    return [
        HTTP_TEMPLATE.format(hostname=domain, webroot_dir=settings.SSL_WEBROOT_DIR, body=http_body),
        HTTPS_TEMPLATE.format(
            hostname=domain, cert_path=ssl_module.cert_path(domain), key_path=ssl_module.key_path(domain),
            body=https_body,
        ),
    ]


def _blocks_for_site(site):
    default_domain = site.default_domain
    return [
        block
        for domain in site.all_domains
        for block in _blocks_for_domain(
            domain,
            is_default=(domain == default_domain),
            default_domain=default_domain,
            site_hostname=site.hostname,
            bench_container=site.bench.container_name,
        )
    ]


def regenerate_and_reload():
    """Rewrite the shared per-site nginx conf file and reload the proxy.

    Serialized with a short-lived Redis lock so concurrent site/bench
    changes don't race each other writing the same file.
    """
    from sites.models import Site  # avoid an import cycle at module load time

    lock = _redis_client().lock(LOCK_KEY, timeout=LOCK_TIMEOUT)
    with lock:
        active_sites = Site.objects.filter(status=Site.STATUS_ACTIVE).select_related('bench').prefetch_related('domains')
        blocks = [block for site in active_sites for block in _blocks_for_site(site)]
        config = '\n'.join(blocks)

        conf_path = os.path.join(settings.NGINX_CONF_DIR, 'sites.conf')
        with open(conf_path, 'w', encoding='utf-8') as handle:
            handle.write(config)

        exit_code, output = run_command(settings.PROXY_CONTAINER_NAME, ['nginx', '-s', 'reload'])
        if exit_code != 0:
            raise RuntimeError(f'nginx reload failed: {output}')
