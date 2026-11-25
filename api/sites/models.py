"""Models for the app catalog, sites, backups, and async job tracking."""
from django.conf import settings
from django.db import models
from django.utils import timezone


def is_hostname_allowed(hostname):
    """Check a candidate site hostname against settings.ALLOWED_SITE_DOMAINS.

    Allowed if it's exactly one of the configured domains or any
    subdomain of one — so a domain entry of "kartoza.com" covers both
    "kartoza.com" itself and "shop.kartoza.com".
    """
    hostname = hostname.lower()
    return any(
        hostname == domain or hostname.endswith(f'.{domain}')
        for domain in settings.ALLOWED_SITE_DOMAINS
    )


class App(models.Model):
    """An installable Frappe app (e.g. ERPNext, HRMS).

    No notion of "which branches this app supports" is tracked here — the
    operator picks the exact branch to fetch each time, on the bench
    they're fetching it onto (see ``BenchApp.branch``), since an app's
    branch naming doesn't necessarily follow Frappe's own version
    convention and shouldn't be auto-derived or restricted.
    """

    name = models.CharField(max_length=100, unique=True)
    repo_url = models.URLField()
    # The catalog name above is whatever the operator typed when adding the
    # app, which doesn't always match the name `bench` actually registers it
    # under (e.g. catalog "kartoza_portfolio" repo installs as "portfolio") —
    # that real name comes from the cloned repo's own hooks.py, not its repo
    # name. Populated automatically the first time `get_app` fetches it
    # successfully (see bench_exec.get_app); blank until then.
    actual_name = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def resolved_name(self):
        """The name to pass to `bench` CLI commands — real name once known, catalog name otherwise."""
        return self.actual_name or self.name


class Site(models.Model):
    """A single Frappe/ERPNext site hosted on one bench."""

    STATUS_CREATING = 'creating'
    STATUS_ACTIVE = 'active'
    STATUS_SUSPENDED = 'suspended'
    STATUS_ERROR = 'error'
    STATUS_DELETING = 'deleting'
    STATUS_CHOICES = [
        (STATUS_CREATING, 'Creating'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_ERROR, 'Error'),
        (STATUS_DELETING, 'Deleting'),
    ]

    bench = models.ForeignKey(
        'benches.Bench', on_delete=models.CASCADE, related_name='sites'
    )
    hostname = models.CharField(max_length=255, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_CREATING
    )
    # v1 limitation: stored in plain text so the platform can hand it back to
    # the operator after creation (e.g. for the first login). Treat the
    # platform database itself as sensitive; do not expose this field over
    # the API after initial creation.
    admin_password = models.CharField(max_length=255)
    maintenance_mode = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.hostname

    def save(self, *args, **kwargs):
        """Force hostnames lowercase (browsers lowercase Host headers)."""
        self.hostname = self.hostname.lower()
        super().save(*args, **kwargs)

    @property
    def default_domain(self):
        """The domain that should actually serve this site's content.

        Defaults to the site's own (immutable) hostname until an operator
        explicitly marks a custom domain as default — every other domain
        this site answers to just redirects here (see nginx_config.py).
        """
        default = self.domains.filter(is_default=True).first()
        return default.domain if default else self.hostname

    @property
    def all_domains(self):
        """Every hostname this site answers to: its own plus any custom domains."""
        return [self.hostname, *self.domains.values_list('domain', flat=True)]


class SiteDomain(models.Model):
    """An additional custom domain (CNAME) pointed at a site.

    The site's own ``hostname`` is fixed at creation time (it's literally
    the folder/db name Frappe uses on disk) and is never represented as a
    row here — this table only holds *extra* domains layered on top of
    that, any one of which can be marked the default (see
    ``Site.default_domain``) without touching the real hostname at all.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='domains')
    domain = models.CharField(max_length=255, unique=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Partial unique index: at most one default per site, but any
            # number of non-default ones (a plain unique_together on
            # (site, is_default) would also forbid a second *non*-default
            # domain on the same site).
            models.UniqueConstraint(
                fields=['site'], condition=models.Q(is_default=True),
                name='unique_default_domain_per_site',
            ),
        ]

    def __str__(self):
        return self.domain

    def save(self, *args, **kwargs):
        """Force domains lowercase, mirroring Site.hostname."""
        self.domain = self.domain.lower()
        super().save(*args, **kwargs)


class SiteApp(models.Model):
    """An app installed on a site."""

    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name='installed_apps'
    )
    app = models.ForeignKey(App, on_delete=models.CASCADE)
    installed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('site', 'app')

    def __str__(self):
        return f'{self.app.name} on {self.site.hostname}'


class BenchApp(models.Model):
    """An app fetched onto a bench (bench-wide, independent of any site).

    `bench get-app` is a bench-wide operation (one shared `apps/` dir), so
    this is tracked once per (bench, app) rather than per-site, mirroring
    how `SiteApp` tracks per-site installs. ``branch`` records which exact
    branch was fetched — chosen explicitly by the operator each time, not
    derived from the bench's own Frappe branch.
    """

    bench = models.ForeignKey(
        'benches.Bench', on_delete=models.CASCADE, related_name='fetched_apps'
    )
    app = models.ForeignKey(App, on_delete=models.CASCADE)
    branch = models.CharField(max_length=100, default='')
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('bench', 'app')

    def __str__(self):
        return f'{self.app.name}@{self.branch} on {self.bench.name}'


class Backup(models.Model):
    """A database + files backup of a site."""

    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='backups')
    file_path = models.CharField(max_length=500, blank=True, default='')
    size_bytes = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Backup of {self.site.hostname} @ {self.created_at:%Y-%m-%d %H:%M}'


class Job(models.Model):
    """An async unit of work (container orchestration or bench CLI command)."""

    TYPE_BENCH_CREATE = 'bench_create'
    TYPE_BENCH_DELETE = 'bench_delete'
    TYPE_SITE_CREATE = 'site_create'
    TYPE_SITE_DELETE = 'site_delete'
    TYPE_APP_FETCH = 'app_fetch'
    TYPE_APP_INSTALL = 'app_install'
    TYPE_APP_UNINSTALL = 'app_uninstall'
    TYPE_APP_UPDATE = 'app_update'
    TYPE_APP_REMOVE = 'app_remove'
    TYPE_SITE_MIGRATE = 'site_migrate'
    TYPE_SITE_BACKUP = 'site_backup'
    TYPE_SITE_RESTORE = 'site_restore'
    TYPE_RESTORE_DATABASE = 'restore_database'
    TYPE_RESTORE_SITE_CONFIG = 'restore_site_config'
    TYPE_RESTORE_PUBLIC_FILES = 'restore_public_files'
    TYPE_RESTORE_PRIVATE_FILES = 'restore_private_files'
    TYPE_MAINTENANCE_TOGGLE = 'maintenance_toggle'
    TYPE_CHOICES = [
        (TYPE_BENCH_CREATE, 'Bench create'),
        (TYPE_BENCH_DELETE, 'Bench delete'),
        (TYPE_SITE_CREATE, 'Site create'),
        (TYPE_SITE_DELETE, 'Site delete'),
        (TYPE_APP_FETCH, 'App fetch'),
        (TYPE_APP_INSTALL, 'App install'),
        (TYPE_APP_UNINSTALL, 'App uninstall'),
        (TYPE_APP_UPDATE, 'App update'),
        (TYPE_APP_REMOVE, 'App remove'),
        (TYPE_SITE_MIGRATE, 'Site migrate'),
        (TYPE_SITE_BACKUP, 'Site backup'),
        (TYPE_SITE_RESTORE, 'Site restore'),
        (TYPE_RESTORE_DATABASE, 'Restore database from upload'),
        (TYPE_RESTORE_SITE_CONFIG, 'Restore site config from upload'),
        (TYPE_RESTORE_PUBLIC_FILES, 'Restore public files from upload'),
        (TYPE_RESTORE_PRIVATE_FILES, 'Restore private files from upload'),
        (TYPE_MAINTENANCE_TOGGLE, 'Maintenance toggle'),
    ]

    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    job_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    bench = models.ForeignKey(
        'benches.Bench',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
    )
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='jobs'
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED
    )
    logs = models.TextField(blank=True, default='')
    log_truncated = models.BooleanField(default=False)
    log_file_path = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.job_type} [{self.status}]'

    def append_log(self, text):
        """Append output to this job's log, truncating if it grows too large.

        Called frequently while a command streams output, so it only writes
        the fields that actually changed.
        """
        max_chars = settings.JOB_LOG_MAX_CHARS
        self.logs += text
        if len(self.logs) > max_chars:
            self.logs = self.logs[-max_chars:]
            self.log_truncated = True
        self.save(update_fields=['logs', 'log_truncated'])

    def mark_running(self):
        """Transition this job to the running state."""
        self.status = self.STATUS_RUNNING
        self.save(update_fields=['status'])

    def mark_success(self):
        """Transition this job to the success state."""
        self.status = self.STATUS_SUCCESS
        self.finished_at = timezone.now()
        self.save(update_fields=['status', 'finished_at'])

    def mark_failed(self, message=''):
        """Transition this job to the failed state, recording an error message."""
        self.status = self.STATUS_FAILED
        if message:
            self.append_log(f'\nERROR: {message}\n')
        self.finished_at = timezone.now()
        self.save(update_fields=['status', 'finished_at'])
