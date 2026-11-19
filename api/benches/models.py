"""Models for servers and bench units.

A ``Bench`` is a single Frappe bench pinned to one Frappe/ERPNext branch,
running as its own set of containers (bench + redis-cache + redis-queue) so
that multiple benches on different versions can coexist on one server.
"""
from django.db import models


class Server(models.Model):
    """The physical/virtual host the platform manages.

    .. note::
        v1 only ever has a single row (the host the platform itself runs
        on). The field exists so a future multi-server version has
        somewhere to attach without reshaping this model.
    """

    name = models.CharField(max_length=255, default='localhost')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Bench(models.Model):
    """A single Frappe bench, pinned to one Frappe/ERPNext branch."""

    STATUS_CREATING = 'creating'
    STATUS_ACTIVE = 'active'
    STATUS_ERROR = 'error'
    STATUS_DELETING = 'deleting'
    STATUS_CHOICES = [
        (STATUS_CREATING, 'Creating'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_ERROR, 'Error'),
        (STATUS_DELETING, 'Deleting'),
    ]

    server = models.ForeignKey(
        Server, on_delete=models.CASCADE, related_name='benches'
    )
    name = models.CharField(max_length=100, unique=True)
    frappe_branch = models.CharField(max_length=100, default='version-15')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_CREATING
    )
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name} ({self.frappe_branch})'

    @property
    def container_name(self):
        """Name of this bench's main container."""
        return f'bench-{self.pk}'

    @property
    def redis_cache_container_name(self):
        """Name of this bench's dedicated redis-cache container."""
        return f'redis-cache-{self.pk}'

    @property
    def redis_queue_container_name(self):
        """Name of this bench's dedicated redis-queue container."""
        return f'redis-queue-{self.pk}'

    @property
    def volume_name(self):
        """Name of the Docker volume holding this bench's data."""
        return f'bench-data-{self.pk}'

    @property
    def bench_path(self):
        """Absolute path to the bench's frappe-bench dir inside its container."""
        return '/home/frappe/frappe-bench'
