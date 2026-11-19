"""API views for servers and benches."""
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Bench, Server
from .serializers import BenchSerializer
from .tasks import create_bench_task, delete_bench_task


class BenchViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List/create/retrieve/delete bench units.

    Benches can't be edited in place (their branch is fixed at creation
    time), so this deliberately excludes update/partial_update.
    """

    queryset = Bench.objects.all().order_by('-created_at').prefetch_related('fetched_apps__app')
    serializer_class = BenchSerializer

    def perform_create(self, serializer):
        from sites.models import Job

        server, _ = Server.objects.get_or_create(
            name='localhost', defaults={'name': 'localhost'}
        )
        bench = serializer.save(server=server, status=Bench.STATUS_CREATING)
        job = Job.objects.create(job_type=Job.TYPE_BENCH_CREATE, bench=bench)
        create_bench_task.delay(bench.id, job.id)
        self._created_job_id = job.id

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data['job_id'] = self._created_job_id
        return response

    def destroy(self, request, *args, **kwargs):
        from sites.models import Job, Site

        bench = self.get_object()
        active_sites = Site.objects.filter(bench=bench).exclude(
            status=Site.STATUS_DELETING
        )
        if active_sites.exists():
            return Response(
                {'detail': 'Cannot delete a bench that still has sites on it.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        bench.status = Bench.STATUS_DELETING
        bench.save(update_fields=['status'])
        job = Job.objects.create(job_type=Job.TYPE_BENCH_DELETE, bench=bench)
        delete_bench_task.delay(bench.id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='fetch-app')
    def fetch_app(self, request, pk=None):
        """Fetch an app from the catalog onto this bench, at a branch the operator picks.

        No restriction on which branch is allowed here — the operator
        chooses it explicitly (see AppViewSet.branches for listing a
        repo's real branches), independent of this bench's own Frappe
        branch.
        """
        from sites.models import App, Job
        from sites.tasks import fetch_app_task

        bench = self.get_object()
        app_id = request.data.get('app_id')
        branch = request.data.get('branch')
        if not app_id:
            return Response(
                {'detail': 'app_id is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        if not branch:
            return Response(
                {'detail': 'branch is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        app = get_object_or_404(App, pk=app_id)

        job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=bench)
        fetch_app_task.delay(bench.id, app_id, job.id, branch)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='update-app')
    def update_app(self, request, pk=None):
        """Pull an app's latest code bench-wide at a chosen branch, then migrate every site that has it.

        ``branch`` defaults to whatever's already fetched for this app on
        this bench; passing a different one switches branches. The code
        update itself always affects every site on the bench (shared
        `apps/` dir), but only sites with this specific app installed get
        migrated afterwards.
        """
        from sites.models import App, BenchApp, Job
        from sites.tasks import update_app_task

        bench = self.get_object()
        app_id = request.data.get('app_id')
        if not app_id:
            return Response(
                {'detail': 'app_id is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        app = get_object_or_404(App, pk=app_id)

        branch = request.data.get('branch')
        if not branch:
            bench_app = BenchApp.objects.filter(bench=bench, app=app).first()
            if not bench_app:
                return Response(
                    {'detail': f'{app.name} is not fetched on this bench yet.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            branch = bench_app.branch

        affected_sites = list(
            bench.sites.filter(installed_apps__app=app)
            .exclude(status='deleting')
            .values_list('hostname', flat=True)
        )
        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=bench)
        update_app_task.delay(bench.id, app_id, job.id, branch)
        return Response(
            {'job_id': job.id, 'affected_sites': affected_sites, 'branch': branch},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'], url_path='remove-app')
    def remove_app(self, request, pk=None):
        """Remove an app from this bench entirely, deleting its code bench-wide.

        Blocked while any site on this bench still has the app installed
        — uninstall it from those sites first (see SiteViewSet.uninstall_app).
        """
        from sites.models import App, BenchApp, Job
        from sites.tasks import remove_app_task

        bench = self.get_object()
        app_id = request.data.get('app_id')
        if not app_id:
            return Response(
                {'detail': 'app_id is required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        app = get_object_or_404(App, pk=app_id)
        if not BenchApp.objects.filter(bench=bench, app=app).exists():
            return Response(
                {'detail': f'{app.name} is not fetched on this bench.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sites_with_app = list(
            bench.sites.filter(installed_apps__app=app)
            .exclude(status='deleting')
            .values_list('hostname', flat=True)
        )
        if sites_with_app:
            return Response(
                {
                    'detail': (
                        f"Can't remove {app.name}: still installed on "
                        f"{', '.join(sites_with_app)}. Uninstall it from those sites first."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = Job.objects.create(job_type=Job.TYPE_APP_REMOVE, bench=bench)
        remove_app_task.delay(bench.id, app_id, job.id)
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)
