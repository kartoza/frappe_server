"""Wrappers around specific ``bench`` CLI invocations.

Each function runs one bench command inside the target bench's container,
streaming output into the given Job's log, and raises on a non-zero exit
code so the calling Celery task can mark the job failed.
"""
import json

from django.conf import settings

from benches import container_exec
from benches.container_exec import exec_stream, run_command

BENCH_USER = 'frappe'


def _run(bench, args, job, workdir=None):
    workdir = workdir or bench.bench_path
    command = ['bench'] + args
    exit_code = exec_stream(
        bench.container_name,
        command,
        user=BENCH_USER,
        workdir=workdir,
        on_output=job.append_log,
    )
    if exit_code != 0:
        raise RuntimeError(f"Command failed (exit {exit_code}): {' '.join(command)}")


def app_is_fetched(bench, app):
    """Return True if ``app`` is cloned *and* actually importable in this bench.

    Checking only for the ``apps/<name>`` directory isn't enough: a
    previous fetch interrupted mid-way (e.g. a worker crash during the
    ``pip install`` step) leaves the directory in place from `git clone`
    but without the package actually installed, so it still can't be
    imported. Importing it directly in the bench's own venv is the same
    check Frappe itself relies on when installing an app onto a site, so
    it won't disagree with what `install-app` sees.
    """
    exit_code, _ = run_command(
        bench.container_name,
        [f'{bench.bench_path}/env/bin/python', '-c', f'import {app.resolved_name}'],
        user=BENCH_USER,
    )
    return exit_code == 0


def _read_apps_txt(bench):
    """Return the set of app names bench currently has registered for this bench."""
    exit_code, output = run_command(
        bench.container_name,
        ['cat', f'{bench.bench_path}/sites/apps.txt'],
        user=BENCH_USER,
    )
    if exit_code != 0:
        return set()
    return {line.strip() for line in output.splitlines() if line.strip()}


def _record_actual_name(app, before, after, job):
    """Discover and persist the real bench-registered name for a newly fetched app.

    The catalog name (what the operator typed) doesn't always match what
    `bench get-app` actually registers — it derives the real name from the
    cloned repo's own hooks.py, not the repo name or the argument passed in.
    `apps.txt` is bench's own source of truth for that, so diff it
    before/after the fetch to catch whatever name just got added, and
    record it for every later command (install-app, uninstall-app,
    remove-app, update) that needs the real name rather than the catalog one.
    """
    new_names = after - before
    if len(new_names) != 1:
        return
    real_name = new_names.pop()
    if real_name != app.resolved_name:
        app.actual_name = real_name
        app.save(update_fields=['actual_name'])
        job.append_log(f'Note: "{app.name}" is registered with bench as "{real_name}".\n')


def get_app(bench, app, branch, job):
    """Fetch an app onto the bench at an explicitly chosen branch.

    The branch is whatever the operator picked — there's no attempt to
    derive or restrict it from the bench's own ``frappe_branch``; an app's
    branch naming doesn't necessarily follow Frappe's version convention.

    This is a bench-wide operation (it mutates the shared ``apps/`` dir for
    every site on this bench). Skipped entirely if this app is already
    fetched at this exact branch and genuinely usable; otherwise (not
    fetched, partially fetched, or checked out at a different branch) runs
    with ``--overwrite`` so any of those states converge on the requested
    one.

    Deliberately doesn't restart the bench container: that takes every
    site on it down, however briefly. A fetch-only call has no site to
    migrate yet (the caller installs/migrates the relevant site next), so
    there's nothing more to do here.
    """
    from .models import BenchApp

    already_on_branch = BenchApp.objects.filter(bench=bench, app=app, branch=branch).exists()
    if already_on_branch and app_is_fetched(bench, app):
        job.append_log(f'{app.name} is already fetched on this bench at branch "{branch}", skipping.\n')
        return
    before = _read_apps_txt(bench)
    _run(bench, ['get-app', '--branch', branch, '--overwrite', app.name, app.repo_url], job)
    after = _read_apps_txt(bench)
    _record_actual_name(app, before, after, job)


def new_site(bench, site, job):
    """Create a new, empty Frappe site on the given bench."""
    _run(
        bench,
        [
            'new-site', site.hostname,
            '--mariadb-root-password', settings.MARIADB_ROOT_PASSWORD,
            '--admin-password', site.admin_password,
            '--no-mariadb-socket',
        ],
        job,
    )


def drop_site(bench, site, job):
    """Permanently delete a site and its database."""
    _run(
        bench,
        [
            'drop-site', site.hostname,
            '--mariadb-root-password', settings.MARIADB_ROOT_PASSWORD,
            '--force', '--no-backup',
        ],
        job,
    )


def install_app(bench, site, app, job):
    """Install an already-fetched app onto a site."""
    _run(bench, ['--site', site.hostname, 'install-app', app.resolved_name], job)


def uninstall_app(bench, site, app, job):
    """Uninstall an app from a site, removing its data from that site's database."""
    _run(bench, ['--site', site.hostname, 'uninstall-app', app.resolved_name, '--yes'], job)


def remove_app(bench, app, job):
    """Remove an app from the bench entirely, deleting its code bench-wide.

    Only sensible once nothing on the bench still has it installed — the
    caller is responsible for checking that, this just runs the command.
    """
    _run(bench, ['remove-app', app.resolved_name], job)


def migrate_site(bench, site, job):
    """Run pending migrations for a site."""
    _run(bench, ['--site', site.hostname, 'migrate'], job)


def backup_site(bench, site, job):
    """Back up a site's database and files."""
    _run(bench, ['--site', site.hostname, 'backup', '--with-files'], job)


def restore_site(bench, site, job, db_file, public_files=None, private_files=None):
    """Restore a site from a previous backup. The site must already exist."""
    args = [
        '--site', site.hostname, 'restore', db_file,
        '--mariadb-root-password', settings.MARIADB_ROOT_PASSWORD,
        '--force',
    ]
    if public_files:
        args += ['--with-public-files', public_files]
    if private_files:
        args += ['--with-private-files', private_files]
    _run(bench, args, job)


def set_maintenance_mode(bench, site, enabled, job):
    """Enable or disable maintenance mode for a site."""
    _run(bench, ['--site', site.hostname, 'set-maintenance-mode', 'on' if enabled else 'off'], job)


def update_app(bench, app, branch, job):
    """Pull an app's latest code bench-wide (at an explicitly chosen branch) and rebuild assets.

    ``branch`` is picked by the operator each time — typically the branch
    already recorded for this (bench, app), but switching to a different
    one here is exactly how a branch switch happens.

    Deliberately doesn't restart the bench container — that would take
    every site on it down, not just the ones affected by this app. The
    caller (``update_app_task``) runs ``migrate_site`` on each site that
    has this app installed right after this returns, which is what
    actually brings those sites' schema/cache in line with the new code
    while leaving every site on the bench up throughout.
    """
    _run(bench, ['get-app', '--branch', branch, '--overwrite', app.resolved_name], job)
    _run(bench, ['build'], job)


def list_backups(bench, site):
    """List backup files present in a site's private/backups directory."""
    backups_dir = f'{bench.bench_path}/sites/{site.hostname}/private/backups'
    exit_code, output = run_command(
        bench.container_name, ['ls', '-1', backups_dir], user=BENCH_USER
    )
    if exit_code != 0:
        return []
    return [line for line in output.splitlines() if line.strip()]


def stage_uploaded_file(bench, data, filename):
    """Place an uploaded file into the bench's shared uploads/ dir.

    Lets an HTTP-uploaded file reach a `bench` CLI command that expects a
    path on disk inside the container — there's no shared filesystem
    between the API and a bench container otherwise.

    :returns: The file's path inside the container.
    :rtype: str
    """
    staging_dir = f'{bench.bench_path}/uploads'
    run_command(bench.container_name, ['mkdir', '-p', staging_dir], user=BENCH_USER)
    container_exec.put_bytes(bench.container_name, staging_dir, filename, data)
    return f'{staging_dir}/{filename}'


def restore_database(bench, site, job, remote_sql_path):
    """Restore a site's database from an already-staged SQL dump."""
    _run(
        bench,
        [
            '--site', site.hostname, 'restore', remote_sql_path,
            '--mariadb-root-password', settings.MARIADB_ROOT_PASSWORD,
            '--force',
        ],
        job,
    )


def restore_site_config(bench, site, data):
    """Overwrite a site's site_config.json with uploaded content."""
    target_dir = f'{bench.bench_path}/sites/{site.hostname}'
    container_exec.put_bytes(bench.container_name, target_dir, 'site_config.json', data)


def get_db_name(bench, site):
    """Read a site's own db_name out of its site_config.json, or None.

    Frappe derives the db name from a hash of the site name at creation
    time — nothing on the ``Site`` row itself records it — so this is the
    only way to label a raw MariaDB database with which site it actually
    belongs to (see the SQL playground's database list).
    """
    exit_code, output = run_command(
        bench.container_name,
        ['cat', f'{bench.bench_path}/sites/{site.hostname}/site_config.json'],
        user=BENCH_USER,
    )
    if exit_code != 0:
        return None
    try:
        return json.loads(output).get('db_name')
    except ValueError:
        return None


def _strip_before_marker(marker):
    """Build a path-transform that drops everything up to and including ``marker``.

    A real `bench backup --with-files` tar's entries are nested like
    ``./<site>/public/files/some-file.png`` — extracting that path as-is
    into the site's own ``public/files/`` dir would recreate the nested
    ``<site>/public/files/`` structure *inside* itself. Stripping
    everything through the last occurrence of the marker lands the file
    at the right place either way; archives that don't contain the
    marker (e.g. a plain user-made zip of just the files) are left
    untouched since there's nothing to strip.
    """
    def transform(name):
        idx = name.rfind(marker)
        return name[idx + len(marker):] if idx != -1 else name
    return transform


def _restore_files(bench, target_dir, archive_bytes, archive_format, marker):
    run_command(bench.container_name, ['mkdir', '-p', target_dir], user=BENCH_USER)
    transform_name = _strip_before_marker(marker)
    if archive_format == 'tar':
        container_exec.put_tar_contents(
            bench.container_name, target_dir, archive_bytes, transform_name=transform_name
        )
    else:
        container_exec.put_zip_contents(
            bench.container_name, target_dir, archive_bytes, transform_name=transform_name
        )


def restore_public_files(bench, site, archive_bytes, archive_format):
    """Extract an uploaded zip or tar's contents into a site's public/files dir.

    Works with either a flat archive or a genuine bench-backup-style tar
    (see ``_strip_before_marker``).
    """
    target_dir = f'{bench.bench_path}/sites/{site.hostname}/public/files'
    _restore_files(bench, target_dir, archive_bytes, archive_format, marker='public/files/')


def restore_private_files(bench, site, archive_bytes, archive_format):
    """Extract an uploaded zip or tar's contents into a site's private/files dir.

    Works with either a flat archive or a genuine bench-backup-style tar
    (see ``_strip_before_marker``).
    """
    target_dir = f'{bench.bench_path}/sites/{site.hostname}/private/files'
    _restore_files(bench, target_dir, archive_bytes, archive_format, marker='private/files/')


def file_size(bench, path):
    """Return the size in bytes of a file inside the bench container, or None."""
    exit_code, output = run_command(
        bench.container_name, ['stat', '-c%s', path], user=BENCH_USER
    )
    if exit_code != 0:
        return None
    try:
        return int(output.strip())
    except ValueError:
        return None
