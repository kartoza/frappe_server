"""Tests for Job state transitions, Site hostname normalization, nginx
config generation, and app-fetch/install flows."""
import io
import os
import tarfile
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from benches.models import Bench, Server

from . import nginx_config
from .models import App, Job, Site


class JobStateTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )

    def test_append_log_accumulates_and_truncates(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        with self.settings(JOB_LOG_MAX_CHARS=10):
            job.append_log('0123456789abcdef')
        job.refresh_from_db()
        self.assertEqual(len(job.logs), 10)
        self.assertTrue(job.log_truncated)

    def test_mark_running_then_success_transitions(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        job.mark_running()
        self.assertEqual(job.status, Job.STATUS_RUNNING)

        job.mark_success()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)
        self.assertIsNotNone(job.finished_at)

    def test_mark_failed_records_message_in_logs(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        job.mark_failed('boom')
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn('boom', job.logs)


class SiteHostnameTests(TestCase):
    def test_hostname_is_lowercased_on_save(self):
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a')
        site = Site.objects.create(
            bench=bench, hostname='MySite.Localhost', admin_password='pw'
        )
        self.assertEqual(site.hostname, 'mysite.localhost')


class NginxConfigTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )

    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_regenerate_writes_one_block_per_active_site(
        self, mock_redis_client, mock_run_command
    ):
        active_site = Site.objects.create(
            bench=self.bench,
            hostname='active.localhost',
            admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        Site.objects.create(
            bench=self.bench,
            hostname='pending.localhost',
            admin_password='pw',
            status=Site.STATUS_CREATING,
        )

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        self.assertIn(active_site.hostname, content)
        self.assertNotIn('pending.localhost', content)
        self.assertIn(self.bench.container_name, content)
        mock_run_command.assert_called_once()


class SiteAppMustBeFetchedTests(APITestCase):
    """An app can only be installed on a site if it's already fetched onto
    that site's bench — no auto-fetch, no branch-matching restriction.
    Branch choice happens explicitly when fetching onto the bench."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-14',
            status=Bench.STATUS_ACTIVE,
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

    @patch('sites.views.create_site_task')
    def test_create_site_rejects_app_not_fetched_on_bench(self, mock_task):
        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.localhost',
            'admin_password': 'pw',
            'app_ids': [self.app.id],
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()
        self.assertFalse(Site.objects.filter(hostname='demo.localhost').exists())

    @patch('sites.views.create_site_task')
    def test_create_site_accepts_app_already_fetched_on_bench(self, mock_task):
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='some-branch')

        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.localhost',
            'admin_password': 'pw',
            'app_ids': [self.app.id],
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_task.delay.assert_called_once()

    @patch('sites.views.install_app_task')
    def test_install_app_action_rejects_app_not_fetched_on_bench(self, mock_task):
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        response = self.client.post(
            f'/api/sites/{site.id}/install-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.install_app_task')
    def test_install_app_action_accepts_app_already_fetched_on_bench(self, mock_task):
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='some-branch')
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        response = self.client.post(
            f'/api/sites/{site.id}/install-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()


class OrphanedJobReconciliationTests(TestCase):
    def test_running_jobs_are_marked_failed_queued_jobs_are_untouched(self):
        from platform_api.celery import reconcile_orphaned_jobs

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a')
        running_job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=bench)
        running_job.mark_running()
        queued_job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=bench)

        reconcile_orphaned_jobs()

        running_job.refresh_from_db()
        queued_job.refresh_from_db()
        self.assertEqual(running_job.status, Job.STATUS_FAILED)
        self.assertIn('restarted', running_job.logs)
        self.assertEqual(queued_job.status, Job.STATUS_QUEUED)


class AppFetchRepairTests(TestCase):
    """A partially-fetched app (dir cloned, package not installed — e.g. a
    worker crash mid-``pip install``) must be detected and re-fetched, not
    mistaken for already being usable."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=self.bench)

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command', return_value=(1, ''))
    def test_get_app_refetches_with_overwrite_when_not_importable(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_called_once()
        command = mock_exec_stream.call_args[0][1]
        self.assertIn('--overwrite', command)
        self.assertIn('version-15', command)

    @patch('sites.bench_exec.exec_stream')
    def test_get_app_skips_when_already_fetched_at_same_branch_and_importable(
        self, mock_exec_stream
    ):
        from . import bench_exec
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='version-15')
        with patch('sites.bench_exec.run_command', return_value=(0, '')):
            bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_not_called()

    @patch('sites.bench_exec.exec_stream', return_value=0)
    def test_get_app_refetches_when_requested_branch_differs(self, mock_exec_stream):
        from . import bench_exec
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='version-14')
        with patch('sites.bench_exec.run_command', return_value=(0, '')):
            bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_called_once()
        command = mock_exec_stream.call_args[0][1]
        self.assertIn('version-15', command)


class AppActualNameResolutionTests(TestCase):
    """The catalog name an operator types doesn't always match the name
    bench actually registers an app under (bench derives it from the
    cloned repo's own hooks.py). get_app must discover that real name via
    apps.txt and record it, and every other command that names an
    already-fetched app must use the resolved name, not the catalog one."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(
            name='kartoza_portfolio', repo_url='https://example.com/kartoza_portfolio'
        )
        self.job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=self.bench)

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command')
    def test_get_app_records_actual_name_when_apps_txt_reveals_a_mismatch(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        mock_run_command.side_effect = [
            (0, 'frappe\nerpnext\n'),  # apps.txt before the fetch
            (0, 'frappe\nerpnext\nportfolio\n'),  # apps.txt after the fetch
        ]

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        self.app.refresh_from_db()
        self.assertEqual(self.app.actual_name, 'portfolio')
        self.assertEqual(self.app.resolved_name, 'portfolio')

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command')
    def test_get_app_leaves_actual_name_blank_when_catalog_name_already_matches(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        mock_run_command.side_effect = [
            (0, 'frappe\n'),
            (0, 'frappe\nkartoza_portfolio\n'),
        ]

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        self.app.refresh_from_db()
        self.assertEqual(self.app.actual_name, '')
        self.assertEqual(self.app.resolved_name, 'kartoza_portfolio')

    def test_install_uninstall_remove_use_resolved_name_not_catalog_name(self):
        from . import bench_exec

        self.app.actual_name = 'portfolio'
        self.app.save(update_fields=['actual_name'])
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='x'
        )

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.install_app(self.bench, site, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.uninstall_app(self.bench, site, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.remove_app(self.bench, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)


class AppBranchesEndpointTests(APITestCase):
    """The add-app form fetches real branches instead of the operator typing
    them, so this endpoint's parsing/validation needs to be solid."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)

    def test_branches_requires_repo_url(self):
        response = self.client.get('/api/apps/branches/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_branches_rejects_non_http_scheme(self):
        response = self.client.get('/api/apps/branches/?repo_url=file:///etc/passwd')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.views.subprocess.run')
    def test_branches_parses_git_ls_remote_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                'abc123\trefs/heads/version-14\n'
                'def456\trefs/heads/version-15\n'
                'ghi789\trefs/tags/v1.0.0\n'
            ),
            stderr='',
        )
        response = self.client.get('/api/apps/branches/?repo_url=https://example.com/erpnext')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['branches'], ['version-14', 'version-15'])

    @patch('sites.views.subprocess.run')
    def test_branches_reports_unreachable_repo(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(
            128, 'git', stderr='repository not found'
        )
        response = self.client.get('/api/apps/branches/?repo_url=https://example.com/missing')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SiteRestoreUploadTests(APITestCase):
    """Uploading a SQL dump / site_config.json / zip of files to restore a
    site directly, independent of the platform's own tracked backups."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', frappe_branch='version-15')
        self.site = Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE
        )

    def _zip_bytes(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zip_file:
            zip_file.writestr('hello.txt', 'hi')
        return buffer.getvalue()

    def _tar_bytes(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            data = b'hi'
            info = tarfile.TarInfo(name='hello.txt')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    @patch('sites.views.restore_database_from_upload_task')
    def test_restore_database_writes_to_local_scratch_and_enqueues_job(self, mock_task):
        """Staging into the bench container must not happen inline (see
        tasks.restore_database_from_upload_task) — a slow docker call done
        synchronously here previously tripped gunicorn's worker timeout on
        large dumps. The view's only job now is a fast local write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(UPLOAD_STAGING_DIR=tmpdir):
                upload = SimpleUploadedFile('dump.sql', b'-- sql dump', content_type='application/sql')
                response = self.client.post(
                    f'/api/sites/{self.site.id}/restore-database/', {'file': upload}
                )

                self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
                mock_task.delay.assert_called_once()
                call_args = mock_task.delay.call_args[0]
                self.assertEqual(call_args[0], self.site.id)
                self.assertEqual(call_args[1], response.data['job_id'])
                staging_path = call_args[2]
                self.assertEqual(call_args[3], 'dump.sql')
                with open(staging_path, 'rb') as f:
                    self.assertEqual(f.read(), b'-- sql dump')

    def test_restore_database_requires_file(self):
        response = self.client.post(f'/api/sites/{self.site.id}/restore-database/', {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.bench_exec.restore_site_config')
    def test_restore_site_config_accepts_valid_json(self, mock_restore):
        upload = SimpleUploadedFile(
            'site_config.json', b'{"db_name": "x"}', content_type='application/json'
        )
        response = self.client.post(f'/api/sites/{self.site.id}/restore-site-config/', {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once()
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    def test_restore_site_config_rejects_invalid_json(self):
        upload = SimpleUploadedFile('site_config.json', b'not json', content_type='application/json')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-site-config/', {'file': upload})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.bench_exec.restore_public_files')
    def test_restore_public_files_accepts_valid_zip(self, mock_restore):
        upload = SimpleUploadedFile('files.zip', self._zip_bytes(), content_type='application/zip')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-public-files/', {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._zip_bytes(), 'zip')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_RESTORE_PUBLIC_FILES)

    @patch('sites.bench_exec.restore_private_files')
    def test_restore_private_files_accepts_valid_zip(self, mock_restore):
        upload = SimpleUploadedFile('files.zip', self._zip_bytes(), content_type='application/zip')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-private-files/', {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._zip_bytes(), 'zip')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_RESTORE_PRIVATE_FILES)

    @patch('sites.bench_exec.restore_public_files')
    def test_restore_public_files_accepts_valid_tar(self, mock_restore):
        upload = SimpleUploadedFile('files.tar', self._tar_bytes(), content_type='application/x-tar')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-public-files/', {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._tar_bytes(), 'tar')

    @patch('sites.bench_exec.restore_private_files')
    def test_restore_private_files_accepts_valid_tar(self, mock_restore):
        upload = SimpleUploadedFile('files.tar', self._tar_bytes(), content_type='application/x-tar')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-private-files/', {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._tar_bytes(), 'tar')

    def test_restore_public_files_rejects_neither_zip_nor_tar(self):
        upload = SimpleUploadedFile('files.zip', b'not a zip or tar', content_type='application/zip')
        response = self.client.post(f'/api/sites/{self.site.id}/restore-public-files/', {'file': upload})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RestoreDatabaseTaskTests(TestCase):
    """The slow part of a database restore (staging into the bench
    container, then `bench restore`) happens in the Celery task, reading
    from the local scratch file the view wrote — this must clean that
    scratch file up afterwards regardless of success or failure."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        self.job = Job.objects.create(job_type=Job.TYPE_RESTORE_DATABASE, site=self.site, bench=self.bench)

    @patch('sites.tasks.bench_exec.restore_database')
    @patch('sites.tasks.bench_exec.stage_uploaded_file', return_value='/bench/uploads/restore.sql')
    def test_stages_from_scratch_file_and_removes_it_on_success(self, mock_stage, mock_restore):
        from .tasks import restore_database_from_upload_task

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'-- sql dump')
            staging_path = f.name

        restore_database_from_upload_task(self.site.id, self.job.id, staging_path, 'dump.sql')

        mock_stage.assert_called_once()
        stage_args = mock_stage.call_args[0]
        self.assertEqual(stage_args[0], self.bench)
        self.assertEqual(stage_args[1], b'-- sql dump')
        self.assertTrue(stage_args[2].endswith('dump.sql'))
        mock_restore.assert_called_once_with(self.bench, self.site, self.job, '/bench/uploads/restore.sql')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_SUCCESS)
        self.assertFalse(os.path.exists(staging_path))

    @patch('sites.tasks.bench_exec.restore_database', side_effect=RuntimeError('restore failed'))
    @patch('sites.tasks.bench_exec.stage_uploaded_file', return_value='/bench/uploads/restore.sql')
    def test_removes_scratch_file_even_when_restore_fails(self, mock_stage, mock_restore):
        from .tasks import restore_database_from_upload_task

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'-- sql dump')
            staging_path = f.name

        restore_database_from_upload_task(self.site.id, self.job.id, staging_path, 'dump.sql')

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_FAILED)
        self.assertFalse(os.path.exists(staging_path))


class UpdateAppTaskTests(TestCase):
    """Updating an app is bench-wide, but only sites that actually have it
    installed should be migrated afterwards."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

    @patch('sites.tasks.bench_exec.migrate_site')
    @patch('sites.tasks.bench_exec.update_app')
    def test_update_app_task_migrates_only_sites_with_app_installed(
        self, mock_update_app, mock_migrate
    ):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)
        Site.objects.create(
            bench=self.bench, hostname='without-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        update_app_task(self.bench.id, self.app.id, job.id, 'version-15')

        mock_update_app.assert_called_once()
        mock_migrate.assert_called_once_with(self.bench, site_with_app, job)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    @patch('sites.tasks.bench_exec.migrate_site', side_effect=RuntimeError('migration failed'))
    @patch('sites.tasks.bench_exec.update_app')
    def test_update_app_task_fails_job_if_a_site_migration_fails(
        self, mock_update_app, mock_migrate
    ):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        update_app_task(self.bench.id, self.app.id, job.id, 'version-15')

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn('with-app.localhost', job.logs)

    @patch('sites.tasks.bench_exec.update_app', side_effect=RuntimeError('update failed'))
    def test_update_app_task_does_not_migrate_if_update_itself_fails(self, mock_update_app):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        with patch('sites.tasks.bench_exec.migrate_site') as mock_migrate:
            update_app_task(self.bench.id, self.app.id, job.id, 'version-15')
            mock_migrate.assert_not_called()

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)


class RestoreFilesPathStrippingTests(TestCase):
    """A real `bench backup --with-files` tar nests its entries under
    `<site>/public/files/...` — restoring that straight into the site's
    own public/files dir would recreate that nesting inside itself, so
    the known prefix must be stripped."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw'
        )

    def test_strip_before_marker_handles_bench_backup_style_path(self):
        from . import bench_exec

        transform = bench_exec._strip_before_marker('public/files/')
        self.assertEqual(
            transform('./demo.localhost/public/files/image.png'), 'image.png'
        )

    def test_strip_before_marker_leaves_flat_paths_unchanged(self):
        from . import bench_exec

        transform = bench_exec._strip_before_marker('public/files/')
        self.assertEqual(transform('image.png'), 'image.png')

    @patch('sites.bench_exec.run_command', return_value=(0, ''))
    @patch('sites.bench_exec.container_exec.put_tar_contents')
    def test_restore_public_files_passes_a_working_strip_transform(self, mock_put_tar, mock_run_command):
        from . import bench_exec

        bench_exec.restore_public_files(self.bench, self.site, b'tar-bytes', 'tar')

        transform_name = mock_put_tar.call_args.kwargs['transform_name']
        self.assertEqual(
            transform_name('./demo.localhost/public/files/image.png'), 'image.png'
        )

    @patch('sites.bench_exec.run_command', return_value=(0, ''))
    @patch('sites.bench_exec.container_exec.put_tar_contents')
    def test_restore_private_files_strips_private_marker_not_public(self, mock_put_tar, mock_run_command):
        from . import bench_exec

        bench_exec.restore_private_files(self.bench, self.site, b'tar-bytes', 'tar')

        transform_name = mock_put_tar.call_args.kwargs['transform_name']
        self.assertEqual(
            transform_name('./demo.localhost/private/files/secret.txt'), 'secret.txt'
        )
        # A public/files path isn't the private marker, so it's untouched.
        self.assertEqual(
            transform_name('./demo.localhost/public/files/image.png'),
            './demo.localhost/public/files/image.png',
        )


class SiteUninstallAppTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.uninstall_app_task')
    def test_uninstall_app_rejects_app_not_installed(self, mock_task):
        response = self.client.post(
            f'/api/sites/{self.site.id}/uninstall-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.uninstall_app_task')
    def test_uninstall_app_enqueues_job_for_installed_app(self, mock_task):
        from .models import SiteApp

        SiteApp.objects.create(site=self.site, app=self.app)

        response = self.client.post(
            f'/api/sites/{self.site.id}/uninstall-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()


class UninstallAppTaskTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

    @patch('sites.tasks.bench_exec.uninstall_app')
    def test_uninstall_app_task_removes_site_app_record(self, mock_uninstall):
        from .models import SiteApp
        from .tasks import uninstall_app_task

        SiteApp.objects.create(site=self.site, app=self.app)
        job = Job.objects.create(job_type=Job.TYPE_APP_UNINSTALL, site=self.site, bench=self.bench)

        uninstall_app_task(self.site.id, self.app.id, job.id)

        mock_uninstall.assert_called_once()
        self.assertFalse(SiteApp.objects.filter(site=self.site, app=self.app).exists())
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)
