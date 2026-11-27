"""Reconnect and start any bench-unit containers orphaned by a host restart.

Bench containers are created outside docker-compose (see
``benches.docker_client``), so a plain ``docker compose up -d`` never starts
them back up. Worse, recreating the compose project gives
``frappe-platform-net`` a new network ID, so any bench container stopped
before the restart is left pointing at a network that no longer exists.
Run on every API container startup to heal both cases.
"""
import docker
from django.conf import settings
from django.core.management.base import BaseCommand

from benches.container_exec import get_client
from benches.models import Bench


class Command(BaseCommand):
    """Reconnect bench containers to the current network and start them."""

    help = 'Reconnect and start bench-unit containers left stopped/orphaned by a host restart.'

    def handle(self, *args, **options):
        """Run the command."""
        client = get_client()
        network = client.networks.get(settings.DOCKER_NETWORK_NAME)

        container_names = []
        for bench in Bench.objects.all():
            container_names += [
                bench.redis_cache_container_name,
                bench.redis_queue_container_name,
                bench.container_name,
            ]

        for name in container_names:
            try:
                container = client.containers.get(name)
            except docker.errors.NotFound:
                continue

            attached = container.attrs['NetworkSettings']['Networks'].get(settings.DOCKER_NETWORK_NAME)
            # A container stopped before the network was recreated (e.g. by
            # `docker compose down`) still carries an entry under the same
            # network *name*, but it points at the old, now-deleted network
            # *ID* - comparing names alone would wrongly call it attached.
            if attached is None or attached.get('NetworkID') != network.id:
                if attached is not None:
                    network.disconnect(container, force=True)
                network.connect(container)
                self.stdout.write(f'Reconnected {name} to {settings.DOCKER_NETWORK_NAME}.')

            container.reload()
            if container.status != 'running':
                container.start()
                self.stdout.write(self.style.SUCCESS(f'Started {name}.'))
