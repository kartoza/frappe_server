"""Docker orchestration for bench-unit containers.

A "bench unit" is the group of 3 containers (bench, redis-cache, redis-queue)
plus 1 volume that make up a single :class:`benches.models.Bench`. These are
created/removed dynamically via the Docker API rather than declared in
docker-compose.yml, since the set of benches is user-managed at runtime.
"""
import time

import docker
from django.conf import settings

from .container_exec import get_client, run_command

REDIS_IMAGE = 'redis:7-alpine'


def create_bench_containers(bench):
    """Create and start the volume + 3 containers that make up a bench unit."""
    client = get_client()
    network = settings.DOCKER_NETWORK_NAME

    client.volumes.create(name=bench.volume_name)

    client.containers.run(
        REDIS_IMAGE,
        name=bench.redis_cache_container_name,
        network=network,
        detach=True,
        restart_policy={'Name': 'unless-stopped'},
    )
    client.containers.run(
        REDIS_IMAGE,
        name=bench.redis_queue_container_name,
        network=network,
        detach=True,
        restart_policy={'Name': 'unless-stopped'},
    )
    client.containers.run(
        settings.BENCH_IMAGE_TAG,
        name=bench.container_name,
        network=network,
        detach=True,
        restart_policy={'Name': 'unless-stopped'},
        environment={
            'FRAPPE_BRANCH': bench.frappe_branch,
            'MARIADB_HOST': settings.MARIADB_HOST,
            'MARIADB_PORT': '3306',
            'REDIS_CACHE_HOST': bench.redis_cache_container_name,
            'REDIS_QUEUE_HOST': bench.redis_queue_container_name,
            'REDIS_PORT': '6379',
        },
        volumes={bench.volume_name: {'bind': '/home/frappe', 'mode': 'rw'}},
    )


def remove_bench_containers(bench, remove_volume=True):
    """Stop and remove all 3 containers (and optionally the volume) for a bench."""
    client = get_client()
    for name in (
        bench.container_name,
        bench.redis_cache_container_name,
        bench.redis_queue_container_name,
    ):
        try:
            client.containers.get(name).remove(force=True)
        except docker.errors.NotFound:
            pass

    if remove_volume:
        try:
            client.volumes.get(bench.volume_name).remove(force=True)
        except docker.errors.NotFound:
            pass


def wait_for_bench_ready(bench, timeout=300, interval=5):
    """Poll a bench container until its web process accepts connections.

    :raises TimeoutError: if the bench isn't ready within ``timeout`` seconds.
    """
    deadline = time.time() + timeout
    last_error = ''
    while time.time() < deadline:
        try:
            exit_code, _ = run_command(
                bench.container_name,
                ['curl', '-s', '-o', '/dev/null', 'http://localhost:8000'],
            )
            if exit_code == 0:
                return
            last_error = f'curl exited with code {exit_code}'
        except docker.errors.APIError as exc:
            last_error = str(exc)
        time.sleep(interval)

    raise TimeoutError(
        f'Bench {bench.name} did not become ready within {timeout}s: {last_error}'
    )
