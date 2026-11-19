"""Celery application for orchestrating docker-exec'd bench/infra commands."""
import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'platform_api.settings')

app = Celery('platform_api')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@worker_ready.connect
def reconcile_orphaned_jobs(**kwargs):
    """Fail any Job left "running" by a worker process that no longer exists.

    A Celery task's progress only lives in the worker process that's
    executing it — if that process dies (crash, OOM, host reboot) mid-task,
    the Job row is left stuck "running" forever with nothing to resolve it.
    Since a freshly-started worker can't have inherited any in-flight task,
    anything still "running" when *this* worker boots is safe to assume
    orphaned.

    .. note::
        This assumes a single worker process/replica (the v1 deployment
        topology). With multiple worker replicas, one restarting would
        incorrectly fail jobs genuinely in progress on another.
    """
    from sites.models import Job

    orphaned = Job.objects.filter(status=Job.STATUS_RUNNING)
    for job in orphaned:
        job.mark_failed(
            'Worker process restarted while this job was running; its outcome is unknown.'
        )
