"""API views for the app catalog, sites, backups, and jobs."""
import io
import json
import os
import subprocess
import tarfile
import uuid
import zipfile

import pymysql
from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from benches.container_exec import get_client

from . import bench_exec, sql_playground
from .models import App, Backup, Job, Site, SiteApp
from .serializers import AppSerializer, BackupSerializer, JobSerializer, SiteSerializer
from .tasks import (
    backup_site_task,
    create_site_task,
    delete_site_task,
    install_app_task,
    migrate_site_task,
    restore_database_from_upload_task,
    restore_site_task,
    toggle_maintenance_task,
    uninstall_app_task,
)


class AppViewSet(viewsets.ModelViewSet):
    """CRUD for the catalog of installable Frappe apps."""

    queryset = App.objects.all().order_by('name')
    serializer_class = AppSerializer

    @action(detail=False, methods=['get'], url_path='branches')
    def branches(self, request):
        """List the real branches of a git repo.

        Used wherever the operator picks which branch of an app to fetch
        onto a bench — fetched from the remote directly (`git ls-remote`,
        no clone) rather than relying on them to type a branch name by
        hand and hope it exists.
        """
        repo_url = request.query_params.get('repo_url', '')
        if not repo_url:
            return Response(
                {'detail': 'repo_url is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        if not repo_url.startswith(('http://', 'https://')):
            return Response(
                {'detail': 'repo_url must be an http(s) URL.'}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = subprocess.run(
                ['git', 'ls-remote', '--heads', repo_url],
                capture_output=True, text=True, timeout=15, check=True,
            )
        except subprocess.CalledProcessError as exc:
            return Response(
                {'detail': f'Could not reach repository: {exc.stderr.strip() or exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except subprocess.TimeoutExpired:
            return Response(
                {'detail': 'Timed out contacting the repository.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        branch_names = []
        for line in result.stdout.splitlines():
            _, _, ref = line.partition('\t')
            prefix = 'refs/heads/'
            if ref.startswith(prefix):
                branch_names.append(ref[len(prefix):])

        return Response({'branches': sorted(branch_names)})


class SiteViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List/create/retrieve/delete sites, plus bench-CLI action endpoints."""

    serializer_class = SiteSerializer

    def get_queryset(self):
        """Support ?bench=<id> to scope sites to a single bench."""
        queryset = Site.objects.all().select_related('bench').order_by('-created_at')
        bench_id = self.request.query_params.get('bench')
        if bench_id:
            queryset = queryset.filter(bench_id=bench_id)
        return queryset

    def perform_create(self, serializer):
        app_ids = [app.id for app in serializer.validated_data.pop('app_ids', [])]
        site = serializer.save(status=Site.STATUS_CREATING)
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, site=site, bench=site.bench)
        create_site_task.delay(site.id, job.id, app_ids)
        self._created_job_id = job.id

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data['job_id'] = self._created_job_id
        return response

    def destroy(self, request, *args, **kwargs):
        site = self.get_object()
        site.status = Site.STATUS_DELETING
        site.save(update_fields=['status'])
        job = Job.objects.create(job_type=Job.TYPE_SITE_DELETE, site=site, bench=site.bench)
        delete_site_task.delay(site.id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['get'], url_path='allowed-domains')
    def allowed_domains(self, request):
        """Domains (see settings.ALLOWED_SITE_DOMAINS) a new site's hostname must fall under."""
        return Response({'domains': settings.ALLOWED_SITE_DOMAINS})

    @action(detail=True, methods=['post'], url_path='install-app')
    def install_app(self, request, pk=None):
        """Install an already-fetched app onto this site.

        The app must already be fetched onto this site's bench — branch
        choice happens there, explicitly, not here.
        """
        from .models import BenchApp

        site = self.get_object()
        app_id = request.data.get('app_id')
        if not app_id:
            return Response({'detail': 'app_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        app = get_object_or_404(App, pk=app_id)
        if not BenchApp.objects.filter(bench=site.bench, app=app).exists():
            return Response(
                {'detail': f'{app.name} is not fetched on bench "{site.bench.name}" yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = Job.objects.create(job_type=Job.TYPE_APP_INSTALL, site=site, bench=site.bench)
        install_app_task.delay(site.id, app_id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='uninstall-app')
    def uninstall_app(self, request, pk=None):
        """Uninstall an app from this site, removing its data from this site's database."""
        site = self.get_object()
        app_id = request.data.get('app_id')
        if not app_id:
            return Response({'detail': 'app_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        app = get_object_or_404(App, pk=app_id)
        if not SiteApp.objects.filter(site=site, app=app).exists():
            return Response(
                {'detail': f'{app.name} is not installed on this site.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = Job.objects.create(job_type=Job.TYPE_APP_UNINSTALL, site=site, bench=site.bench)
        uninstall_app_task.delay(site.id, app_id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def backup(self, request, pk=None):
        """Trigger a database+files backup of this site."""
        site = self.get_object()
        backup = Backup.objects.create(site=site)
        job = Job.objects.create(job_type=Job.TYPE_SITE_BACKUP, site=site, bench=site.bench)
        backup_site_task.delay(site.id, job.id, backup.id)
        return Response({'job_id': job.id, 'backup_id': backup.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore this site from a previous backup."""
        site = self.get_object()
        backup_id = request.data.get('backup_id')
        if not backup_id:
            return Response({'detail': 'backup_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        job = Job.objects.create(job_type=Job.TYPE_SITE_RESTORE, site=site, bench=site.bench)
        restore_site_task.delay(site.id, backup_id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def migrate(self, request, pk=None):
        """Run pending migrations for this site."""
        site = self.get_object()
        job = Job.objects.create(job_type=Job.TYPE_SITE_MIGRATE, site=site, bench=site.bench)
        migrate_site_task.delay(site.id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='maintenance')
    def toggle_maintenance(self, request, pk=None):
        """Enable/disable maintenance mode for this site (not a container stop/start)."""
        site = self.get_object()
        enabled = bool(request.data.get('enabled'))
        job = Job.objects.create(job_type=Job.TYPE_MAINTENANCE_TOGGLE, site=site, bench=site.bench)
        toggle_maintenance_task.delay(site.id, enabled, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='restore-database')
    def restore_database(self, request, pk=None):
        """Restore this site's database from an uploaded SQL dump.

        Only written to local scratch space here (fast regardless of size).
        Transferring it into the bench container is a slow `docker
        put_archive` call for a multi-gigabyte dump — that, and the actual
        `bench restore`, run in the Celery task instead, so an upload this
        size can't tie up a web worker long enough to hit its timeout.
        """
        site = self.get_object()
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': 'file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        os.makedirs(settings.UPLOAD_STAGING_DIR, exist_ok=True)
        staging_path = os.path.join(settings.UPLOAD_STAGING_DIR, f'{uuid.uuid4()}-{uploaded.name}')
        with open(staging_path, 'wb') as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        job = Job.objects.create(job_type=Job.TYPE_RESTORE_DATABASE, site=site, bench=site.bench)
        restore_database_from_upload_task.delay(site.id, job.id, staging_path, uploaded.name)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='restore-site-config')
    def restore_site_config(self, request, pk=None):
        """Overwrite this site's site_config.json with an uploaded file.

        Just a file write (no long-running bench command), so it completes
        synchronously within the request — still recorded as a Job for a
        consistent history/audit trail with everything else.
        """
        site = self.get_object()
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': 'file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        raw = uploaded.read()
        try:
            json.loads(raw)
        except ValueError:
            return Response(
                {'detail': 'That file is not valid JSON.'}, status=status.HTTP_400_BAD_REQUEST
            )

        job = Job.objects.create(job_type=Job.TYPE_RESTORE_SITE_CONFIG, site=site, bench=site.bench)
        job.mark_running()
        try:
            bench_exec.restore_site_config(site.bench, site, raw)
            job.append_log('site_config.json replaced.\n')
            job.mark_success()
        except Exception as exc:  # noqa: BLE001
            job.mark_failed(str(exc))
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='restore-public-files')
    def restore_public_files(self, request, pk=None):
        """Extract an uploaded zip or tar into this site's public/files directory."""
        return self._restore_files(request, job_type=Job.TYPE_RESTORE_PUBLIC_FILES, is_private=False)

    @action(detail=True, methods=['post'], url_path='restore-private-files')
    def restore_private_files(self, request, pk=None):
        """Extract an uploaded zip or tar into this site's private/files directory."""
        return self._restore_files(request, job_type=Job.TYPE_RESTORE_PRIVATE_FILES, is_private=True)

    def _restore_files(self, request, job_type, is_private):
        site = self.get_object()
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': 'file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        raw = uploaded.read()
        if zipfile.is_zipfile(io.BytesIO(raw)):
            archive_format = 'zip'
        elif tarfile.is_tarfile(io.BytesIO(raw)):
            archive_format = 'tar'
        else:
            return Response(
                {'detail': 'That file is not a valid zip or tar archive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = Job.objects.create(job_type=job_type, site=site, bench=site.bench)
        job.mark_running()
        try:
            if is_private:
                bench_exec.restore_private_files(site.bench, site, raw, archive_format)
            else:
                bench_exec.restore_public_files(site.bench, site, raw, archive_format)
            job.append_log('Files extracted (existing files with the same name were overwritten; others left untouched).\n')
            job.mark_success()
        except Exception as exc:  # noqa: BLE001
            job.mark_failed(str(exc))
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)


class BackupViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """List/retrieve backups, optionally filtered by ?site=<id>, plus download."""

    serializer_class = BackupSerializer

    def get_queryset(self):
        queryset = Backup.objects.all().select_related('site').order_by('-created_at')
        site_id = self.request.query_params.get('site')
        if site_id:
            queryset = queryset.filter(site_id=site_id)
        return queryset

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """Stream a backup's database file straight out of its bench container."""
        backup = self.get_object()
        if not backup.file_path:
            return Response({'detail': 'No backup file recorded.'}, status=status.HTTP_404_NOT_FOUND)

        client = get_client()
        container = client.containers.get(backup.site.bench.container_name)
        remote_path = (
            f'{backup.site.bench.bench_path}/sites/{backup.site.hostname}'
            f'/private/backups/{backup.file_path}'
        )
        stream, _ = container.get_archive(remote_path)
        response = StreamingHttpResponse(stream, content_type='application/x-tar')
        response['Content-Disposition'] = f'attachment; filename="{backup.file_path}.tar"'
        return response


class JobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """List/retrieve async jobs, optionally filtered by ?site=<id> or ?bench=<id>."""

    serializer_class = JobSerializer

    def get_queryset(self):
        queryset = Job.objects.all().order_by('-created_at')
        site_id = self.request.query_params.get('site')
        bench_id = self.request.query_params.get('bench')
        if site_id:
            queryset = queryset.filter(site_id=site_id)
        if bench_id:
            queryset = queryset.filter(bench_id=bench_id)
        return queryset


class SqlDatabasesView(APIView):
    """List the databases the SQL playground can target — admin-only.

    Each is labelled with the site hostname it belongs to where known
    (read from that site's own site_config.json — nothing on the ``Site``
    row itself records its db name, Frappe derives it from a hash of the
    site name at creation time).
    """

    permission_classes = (IsAdminUser,)

    def get(self, request):
        db_names = sql_playground.list_databases()

        hostname_by_db = {}
        active_sites = Site.objects.filter(status=Site.STATUS_ACTIVE).select_related('bench')
        for site in active_sites:
            db_name = bench_exec.get_db_name(site.bench, site)
            if db_name:
                hostname_by_db[db_name] = site.hostname

        databases = [{'name': name, 'site': hostname_by_db.get(name)} for name in db_names]
        return Response({'databases': databases})


class SqlQueryView(APIView):
    """Run arbitrary SQL against a chosen database — admin-only.

    Full read/write access, deliberately: this is a power tool for an
    admin who already has host-level reach via the docker.sock mount
    (see benches/container_exec.py), not a scoped, validated input form.
    """

    permission_classes = (IsAdminUser,)

    def post(self, request):
        database = request.data.get('database')
        sql = request.data.get('sql')
        if not database or not sql:
            return Response(
                {'detail': 'database and sql are both required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            results = sql_playground.run_sql(database, sql)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except pymysql.Error as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'results': results})
