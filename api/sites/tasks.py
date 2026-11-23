"""Celery tasks for site, app-install, backup, restore, and migrate flows."""
import os
import time

from celery import shared_task

from . import bench_exec, nginx_config
from .models import App, Backup, BenchApp, Job, Site, SiteApp


@shared_task
def create_site_task(site_id, job_id, app_ids):
    """Create a site on its bench and install the requested apps onto it.

    Each app must already be fetched onto the bench (with whichever
    branch the operator chose there) — this does not auto-fetch, since
    branch choice is deliberately a bench-level, explicit step.
    """
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    bench = site.bench
    job.mark_running()

    try:
        job.append_log(f'Creating site {site.hostname} on bench {bench.name}...\n')
        bench_exec.new_site(bench, site, job)

        for app in App.objects.filter(pk__in=app_ids):
            if not BenchApp.objects.filter(bench=bench, app=app).exists():
                raise RuntimeError(
                    f'{app.name} is not fetched on bench "{bench.name}" yet. '
                    'Fetch it from the bench page first.'
                )
            job.append_log(f'Installing {app.name} on {site.hostname}...\n')
            bench_exec.install_app(bench, site, app, job)
            SiteApp.objects.get_or_create(site=site, app=app)

        site.status = Site.STATUS_ACTIVE
        site.save(update_fields=['status'])
        nginx_config.regenerate_and_reload()
        job.append_log('Site is active.\n')
        job.mark_success()
    except Exception as exc:  # noqa: BLE001 - must always resolve the job
        site.status = Site.STATUS_ERROR
        site.error_message = str(exc)
        site.save(update_fields=['status', 'error_message'])
        job.mark_failed(str(exc))


@shared_task
def delete_site_task(site_id, job_id):
    """Drop a site's database/files and remove it from nginx routing."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        job.append_log(f'Dropping site {site.hostname}...\n')
        bench_exec.drop_site(site.bench, site, job)
        site.delete()
        nginx_config.regenerate_and_reload()
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        site.status = Site.STATUS_ERROR
        site.error_message = str(exc)
        site.save(update_fields=['status', 'error_message'])
        job.mark_failed(str(exc))


@shared_task
def install_app_task(site_id, app_id, job_id):
    """Install an already-fetched app onto an existing site.

    Does not auto-fetch — the app must already be fetched onto the
    site's bench (with whichever branch the operator chose there).
    """
    site = Site.objects.select_related('bench').get(pk=site_id)
    app = App.objects.get(pk=app_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        if not BenchApp.objects.filter(bench=site.bench, app=app).exists():
            raise RuntimeError(
                f'{app.name} is not fetched on bench "{site.bench.name}" yet. '
                'Fetch it from the bench page first.'
            )
        bench_exec.install_app(site.bench, site, app, job)
        SiteApp.objects.get_or_create(site=site, app=app)
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def uninstall_app_task(site_id, app_id, job_id):
    """Uninstall an app from a site, removing its data from that site's database."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    app = App.objects.get(pk=app_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.uninstall_app(site.bench, site, app, job)
        SiteApp.objects.filter(site=site, app=app).delete()
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def remove_app_task(bench_id, app_id, job_id):
    """Remove an app from a bench entirely, deleting its code bench-wide."""
    from benches.models import Bench

    bench = Bench.objects.get(pk=bench_id)
    app = App.objects.get(pk=app_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.remove_app(bench, app, job)
        BenchApp.objects.filter(bench=bench, app=app).delete()
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def fetch_app_task(bench_id, app_id, job_id, branch):
    """Fetch an app onto a bench at an explicitly chosen branch."""
    from benches.models import Bench

    bench = Bench.objects.get(pk=bench_id)
    app = App.objects.get(pk=app_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.get_app(bench, app, branch, job)
        BenchApp.objects.update_or_create(bench=bench, app=app, defaults={'branch': branch})
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def update_app_task(bench_id, app_id, job_id, branch):
    """Update an app bench-wide at the given branch, then migrate every site that has it installed.

    The code update is bench-wide and atomic from this job's point of
    view (it either fully succeeds or the job fails before touching any
    site). Migrating sites afterwards is best-effort per site: one site
    failing to migrate doesn't stop the others, but the job is still
    reported failed overall so it isn't mistaken for a clean update.
    """
    from benches.models import Bench

    bench = Bench.objects.get(pk=bench_id)
    app = App.objects.get(pk=app_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.update_app(bench, app, branch, job)
        BenchApp.objects.update_or_create(bench=bench, app=app, defaults={'branch': branch})

        sites_with_app = Site.objects.filter(
            bench=bench, installed_apps__app=app
        ).exclude(status=Site.STATUS_DELETING)

        failed_sites = []
        for site in sites_with_app:
            job.append_log(f'Migrating {site.hostname}...\n')
            try:
                bench_exec.migrate_site(bench, site, job)
            except Exception as exc:  # noqa: BLE001
                failed_sites.append(site.hostname)
                job.append_log(f'Migration failed for {site.hostname}: {exc}\n')

        if failed_sites:
            job.mark_failed(
                f"App updated, but migration failed for: {', '.join(failed_sites)}"
            )
        else:
            job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def migrate_site_task(site_id, job_id):
    """Run pending migrations for a single site."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.migrate_site(site.bench, site, job)
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def backup_site_task(site_id, job_id, backup_id):
    """Back up a site and record the resulting file's name/size."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    backup = Backup.objects.get(pk=backup_id)
    job.mark_running()

    try:
        bench_exec.backup_site(site.bench, site, job)
        files = bench_exec.list_backups(site.bench, site)
        db_files = sorted(f for f in files if f.endswith('-database.sql.gz'))
        if db_files:
            latest = db_files[-1]
            backup.file_path = latest
            backup.size_bytes = bench_exec.file_size(
                site.bench,
                f'{site.bench.bench_path}/sites/{site.hostname}/private/backups/{latest}',
            )
        backup.status = Backup.STATUS_SUCCESS
        backup.save()
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        backup.status = Backup.STATUS_FAILED
        backup.save(update_fields=['status'])
        job.mark_failed(str(exc))


@shared_task
def restore_site_task(site_id, backup_id, job_id):
    """Restore a site's database and files from a previous backup."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    backup = Backup.objects.get(pk=backup_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        backups_dir = f'{site.bench.bench_path}/sites/{site.hostname}/private/backups'
        prefix = backup.file_path.replace('-database.sql.gz', '')
        bench_exec.restore_site(
            site.bench, site, job,
            db_file=f'{backups_dir}/{backup.file_path}',
            public_files=f'{backups_dir}/{prefix}-files.tar',
            private_files=f'{backups_dir}/{prefix}-private-files.tar',
        )
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def toggle_maintenance_task(site_id, enabled, job_id):
    """Enable or disable maintenance mode for a site."""
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        bench_exec.set_maintenance_mode(site.bench, site, enabled, job)
        site.maintenance_mode = enabled
        site.save(update_fields=['maintenance_mode'])
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))


@shared_task
def restore_database_from_upload_task(site_id, job_id, staging_path, original_filename):
    """Restore a site's database from a SQL dump the view wrote to local scratch space.

    Transferring it into the bench container happens here, not in the
    request (see SiteViewSet.restore_database) — a slow `docker
    put_archive` call for a large dump, done inline, risks outliving a web
    worker's timeout.
    """
    site = Site.objects.select_related('bench').get(pk=site_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        job.append_log(f'Restoring database for {site.hostname} from uploaded file...\n')
        filename = f'restore-{int(time.time())}-{original_filename}'
        remote_sql_path = bench_exec.stage_uploaded_file(site.bench, staging_path, filename)
        bench_exec.restore_database(site.bench, site, job, remote_sql_path)
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.mark_failed(str(exc))
    finally:
        if os.path.exists(staging_path):
            os.remove(staging_path)


@shared_task
def renew_ssl_certs_task():
    """Periodic (see platform_api/celery.py beat schedule): keep every
    eligible site's certificate issued and renewed.

    Not tracked as a Job — this runs unattended on a schedule rather than
    in response to an operator action, so there's no site/bench to
    attach a Job to and no one watching a log viewer for it. Failures are
    only logged (see sites.ssl.issue_or_renew) since a domain that isn't
    pointed here yet, or has hit a rate limit, is expected to fail
    silently until the next scheduled attempt — it must never raise and
    take the whole beat schedule down with it.
    """
    from . import ssl as ssl_module
    ssl_module.run_for_all_eligible_sites()
