"""Generate and reload the proxy's per-site nginx configuration.

Because sites on different benches share no naming convention that would
let nginx derive "which bench" from a hostname alone, one explicit
server block per active site is generated and written to a file the proxy
container has mounted, then nginx is told to reload.

Every site's HTTP (port 80) block always serves the Let's Encrypt
HTTP-01 challenge path itself, regardless of whether SSL is enabled or
this particular hostname has a certificate yet — needed for the very
first issuance (see sites.ssl) and for every later renewal once the
hostname has its own server block instead of falling through to the
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

PROXY_LOCATIONS = """\
    location /socket.io/ {{
        set $bench_socketio http://{bench_container}:9000;
        proxy_pass $bench_socketio;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
    }}

    location / {{
        set $bench_web http://{bench_container}:8000;
        proxy_pass $bench_web;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }}
"""

# Rendered instead of PROXY_LOCATIONS in the HTTP block once a certificate
# exists — everything but the ACME challenge path itself moves to HTTPS.
REDIRECT_LOCATION = """\
    location / {{
        return 301 https://$host$request_uri;
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

""" + PROXY_LOCATIONS + """\
}}
"""


def _redis_client():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


def _blocks_for_site(hostname, bench_container):
    from . import ssl as ssl_module

    if settings.SSL_ENABLED and ssl_module.has_cert(hostname):
        # .format() with no args still needs to run — it's what collapses
        # this fragment's doubled "{{"/"}}" (needed so *this* string
        # itself is valid when written standalone) down to the literal
        # single braces nginx expects in the final file.
        body = REDIRECT_LOCATION.format()
        blocks = [
            HTTP_TEMPLATE.format(hostname=hostname, webroot_dir=settings.SSL_WEBROOT_DIR, body=body),
            HTTPS_TEMPLATE.format(
                hostname=hostname,
                cert_path=ssl_module.cert_path(hostname),
                key_path=ssl_module.key_path(hostname),
                bench_container=bench_container,
            ),
        ]
    else:
        body = PROXY_LOCATIONS.format(bench_container=bench_container)
        blocks = [HTTP_TEMPLATE.format(hostname=hostname, webroot_dir=settings.SSL_WEBROOT_DIR, body=body)]
    return blocks


def regenerate_and_reload():
    """Rewrite the shared per-site nginx conf file and reload the proxy.

    Serialized with a short-lived Redis lock so concurrent site/bench
    changes don't race each other writing the same file.
    """
    from sites.models import Site  # avoid an import cycle at module load time

    lock = _redis_client().lock(LOCK_KEY, timeout=LOCK_TIMEOUT)
    with lock:
        active_sites = Site.objects.filter(status=Site.STATUS_ACTIVE).select_related('bench')
        blocks = [
            block
            for site in active_sites
            for block in _blocks_for_site(site.hostname, site.bench.container_name)
        ]
        config = '\n'.join(blocks)

        conf_path = os.path.join(settings.NGINX_CONF_DIR, 'sites.conf')
        with open(conf_path, 'w', encoding='utf-8') as handle:
            handle.write(config)

        exit_code, output = run_command(settings.PROXY_CONTAINER_NAME, ['nginx', '-s', 'reload'])
        if exit_code != 0:
            raise RuntimeError(f'nginx reload failed: {output}')
