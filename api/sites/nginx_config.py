"""Generate and reload the proxy's per-site nginx configuration.

Because sites on different benches share no naming convention that would
let nginx derive "which bench" from a hostname alone, one explicit
server block per active site is generated and written to a file the proxy
container has mounted, then nginx is told to reload.
"""
import os

import redis
from django.conf import settings

from benches.container_exec import run_command

LOCK_KEY = 'nginx-config-regen-lock'
LOCK_TIMEOUT = 30

SITE_TEMPLATE = """\
server {{
    listen 80;
    server_name {hostname};

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
}}
"""


def _redis_client():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


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
            SITE_TEMPLATE.format(
                hostname=site.hostname, bench_container=site.bench.container_name
            )
            for site in active_sites
        ]
        config = '\n'.join(blocks)

        conf_path = os.path.join(settings.NGINX_CONF_DIR, 'sites.conf')
        with open(conf_path, 'w', encoding='utf-8') as handle:
            handle.write(config)

        exit_code, output = run_command(settings.PROXY_CONTAINER_NAME, ['nginx', '-s', 'reload'])
        if exit_code != 0:
            raise RuntimeError(f'nginx reload failed: {output}')
