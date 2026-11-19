"""Tests for Bench model helpers and the BenchViewSet API."""
import io
import tarfile
import zipfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from . import container_exec
from .models import Bench, Server


class BenchModelTests(APITestCase):
    def test_container_names_are_derived_from_pk(self):
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.assertEqual(bench.container_name, f'bench-{bench.pk}')
        self.assertEqual(bench.redis_cache_container_name, f'redis-cache-{bench.pk}')
        self.assertEqual(bench.redis_queue_container_name, f'redis-queue-{bench.pk}')
        self.assertEqual(bench.volume_name, f'bench-data-{bench.pk}')


class BenchViewSetTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)

    @patch('benches.views.create_bench_task')
    def test_create_bench_enqueues_a_job_and_returns_job_id(self, mock_task):
        response = self.client.post(
            '/api/benches/', {'name': 'bench-a', 'frappe_branch': 'version-15'}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('job_id', response.data)
        mock_task.delay.assert_called_once()

        bench = Bench.objects.get(name='bench-a')
        self.assertEqual(bench.status, Bench.STATUS_CREATING)

    @patch('benches.views.delete_bench_task')
    def test_destroy_blocks_when_bench_still_has_sites(self, mock_task):
        from sites.models import Site

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', status=Bench.STATUS_ACTIVE
        )
        Site.objects.create(
            bench=bench,
            hostname='demo.localhost',
            admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

        response = self.client.delete(f'/api/benches/{bench.pk}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.tasks.fetch_app_task')
    def test_fetch_app_requires_branch(self, mock_task):
        from sites.models import App

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15', status=Bench.STATUS_ACTIVE
        )
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

        response = self.client.post(
            f'/api/benches/{bench.pk}/fetch-app/', {'app_id': app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.tasks.fetch_app_task')
    def test_fetch_app_enqueues_job_with_chosen_branch(self, mock_task):
        from sites.models import App

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15', status=Bench.STATUS_ACTIVE
        )
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

        response = self.client.post(
            f'/api/benches/{bench.pk}/fetch-app/', {'app_id': app.id, 'branch': 'version-14'}
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()
        self.assertEqual(mock_task.delay.call_args[0][3], 'version-14')

    @patch('sites.tasks.update_app_task')
    def test_update_app_affected_sites_only_lists_sites_with_app_installed(self, mock_task):
        from sites.models import App, BenchApp, Site, SiteApp

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15', status=Bench.STATUS_ACTIVE
        )
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        other_app = App.objects.create(name='hrms', repo_url='https://example.com/hrms')
        BenchApp.objects.create(bench=bench, app=app, branch='version-15')

        site_with_app = Site.objects.create(
            bench=bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=app)

        site_without_app = Site.objects.create(
            bench=bench, hostname='without-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_without_app, app=other_app)

        response = self.client.post(f'/api/benches/{bench.pk}/update-app/', {'app_id': app.id})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['affected_sites'], ['with-app.localhost'])
        self.assertEqual(response.data['branch'], 'version-15')
        mock_task.delay.assert_called_once()

    @patch('sites.tasks.update_app_task')
    def test_update_app_requires_app_to_already_be_fetched_when_no_branch_given(self, mock_task):
        from sites.models import App

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15', status=Bench.STATUS_ACTIVE
        )
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

        response = self.client.post(f'/api/benches/{bench.pk}/update-app/', {'app_id': app.id})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.tasks.update_app_task')
    def test_update_app_allows_explicit_branch_override(self, mock_task):
        from sites.models import App, BenchApp

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15', status=Bench.STATUS_ACTIVE
        )
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        BenchApp.objects.create(bench=bench, app=app, branch='version-15')

        response = self.client.post(
            f'/api/benches/{bench.pk}/update-app/', {'app_id': app.id, 'branch': 'develop'}
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['branch'], 'develop')
        mock_task.delay.assert_called_once_with(
            bench.id, str(app.id), response.data['job_id'], 'develop'
        )

    @patch('sites.tasks.remove_app_task')
    def test_remove_app_rejects_app_not_fetched(self, mock_task):
        from sites.models import App

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

        response = self.client.post(f'/api/benches/{bench.pk}/remove-app/', {'app_id': app.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.tasks.remove_app_task')
    def test_remove_app_blocked_while_installed_on_a_site(self, mock_task):
        from sites.models import App, BenchApp, Site, SiteApp

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        BenchApp.objects.create(bench=bench, app=app, branch='version-15')
        site = Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site, app=app)

        response = self.client.post(f'/api/benches/{bench.pk}/remove-app/', {'app_id': app.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('demo.localhost', response.data['detail'])
        mock_task.delay.assert_not_called()

    @patch('sites.tasks.remove_app_task')
    def test_remove_app_allowed_when_not_installed_anywhere(self, mock_task):
        from sites.models import App, BenchApp

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        BenchApp.objects.create(bench=bench, app=app, branch='version-15')

        response = self.client.post(f'/api/benches/{bench.pk}/remove-app/', {'app_id': app.id})
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()


class ContainerFileUploadTests(TestCase):
    """`put_bytes`/`put_zip_contents` are how an HTTP-uploaded file reaches a
    bench container — there's no shared filesystem otherwise."""

    @patch('benches.container_exec.run_command')
    @patch('benches.container_exec.get_client')
    def test_put_bytes_writes_tar_entry_and_chowns(self, mock_get_client, mock_run_command):
        mock_container = MagicMock()
        mock_get_client.return_value.containers.get.return_value = mock_container

        container_exec.put_bytes('bench-1', '/dest', 'file.txt', b'hello')

        dest_arg, tar_bytes = mock_container.put_archive.call_args[0]
        self.assertEqual(dest_arg, '/dest')
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            self.assertEqual(tar.getnames(), ['file.txt'])
            self.assertEqual(tar.extractfile('file.txt').read(), b'hello')
        mock_run_command.assert_called_once_with(
            'bench-1', ['chown', '-R', 'frappe:frappe', '/dest'], user='root'
        )

    @patch('benches.container_exec.run_command')
    @patch('benches.container_exec.get_client')
    def test_put_zip_contents_converts_zip_to_tar_and_skips_dir_entries(
        self, mock_get_client, mock_run_command
    ):
        mock_container = MagicMock()
        mock_get_client.return_value.containers.get.return_value = mock_container

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
            zip_file.writestr('a.txt', 'contents-a')
            zip_file.writestr('subdir/', '')
            zip_file.writestr('subdir/b.txt', 'contents-b')

        container_exec.put_zip_contents('bench-1', '/dest', zip_buffer.getvalue())

        dest_arg, tar_bytes = mock_container.put_archive.call_args[0]
        self.assertEqual(dest_arg, '/dest')
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            names = tar.getnames()
        self.assertIn('a.txt', names)
        self.assertIn('subdir/b.txt', names)
        mock_run_command.assert_called_once_with(
            'bench-1', ['chown', '-R', 'frappe:frappe', '/dest'], user='root'
        )

    @patch('benches.container_exec.run_command')
    @patch('benches.container_exec.get_client')
    def test_put_tar_contents_passes_through_file_entries(self, mock_get_client, mock_run_command):
        mock_container = MagicMock()
        mock_get_client.return_value.containers.get.return_value = mock_container

        source_buffer = io.BytesIO()
        with tarfile.open(fileobj=source_buffer, mode='w') as source_tar:
            for name, content in [('a.txt', b'contents-a'), ('subdir/b.txt', b'contents-b')]:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                source_tar.addfile(info, io.BytesIO(content))

        container_exec.put_tar_contents('bench-1', '/dest', source_buffer.getvalue())

        dest_arg, tar_bytes = mock_container.put_archive.call_args[0]
        self.assertEqual(dest_arg, '/dest')
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            self.assertEqual(sorted(tar.getnames()), ['a.txt', 'subdir/b.txt'])
            self.assertEqual(tar.extractfile('a.txt').read(), b'contents-a')
        mock_run_command.assert_called_once_with(
            'bench-1', ['chown', '-R', 'frappe:frappe', '/dest'], user='root'
        )

    @patch('benches.container_exec.run_command')
    @patch('benches.container_exec.get_client')
    def test_put_tar_contents_handles_gzip_compressed_tar(self, mock_get_client, mock_run_command):
        mock_container = MagicMock()
        mock_get_client.return_value.containers.get.return_value = mock_container

        source_buffer = io.BytesIO()
        with tarfile.open(fileobj=source_buffer, mode='w:gz') as source_tar:
            content = b'contents-a'
            info = tarfile.TarInfo(name='a.txt')
            info.size = len(content)
            source_tar.addfile(info, io.BytesIO(content))

        container_exec.put_tar_contents('bench-1', '/dest', source_buffer.getvalue())

        _, tar_bytes = mock_container.put_archive.call_args[0]
        # The re-wrapped tar handed to Docker must be plain (uncompressed),
        # since put_archive doesn't expect gzip even if the upload was gzipped.
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:') as tar:
            self.assertEqual(tar.extractfile('a.txt').read(), b'contents-a')

    @patch('benches.container_exec.run_command')
    @patch('benches.container_exec.get_client')
    def test_put_tar_contents_applies_transform_name_and_skips_falsy_results(
        self, mock_get_client, mock_run_command
    ):
        mock_container = MagicMock()
        mock_get_client.return_value.containers.get.return_value = mock_container

        source_buffer = io.BytesIO()
        with tarfile.open(fileobj=source_buffer, mode='w') as source_tar:
            for name, content in [('keep.txt', b'k'), ('drop.txt', b'd')]:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                source_tar.addfile(info, io.BytesIO(content))

        def transform(name):
            return None if name == 'drop.txt' else f'renamed-{name}'

        container_exec.put_tar_contents(
            'bench-1', '/dest', source_buffer.getvalue(), transform_name=transform
        )

        _, tar_bytes = mock_container.put_archive.call_args[0]
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            self.assertEqual(tar.getnames(), ['renamed-keep.txt'])
