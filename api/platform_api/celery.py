"""Celery application for orchestrating docker-exec'd bench/infra commands."""
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'platform_api.settings')

app = Celery('platform_api')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Daily is the interval certbot itself recommends for renewal checks —
# certs are valid 90 days and only actually renew inside their last 30,
# so this is frequent enough to never miss the window while staying well
# clear of Let's Encrypt's rate limits. A no-op whenever SSL_ENABLED is
# off (see sites.ssl.run_for_all_eligible_sites), so it's harmless to
# leave scheduled even on deployments that never turn SSL on.
#
# .. note::
#     Runs embedded in the worker process (``-B`` in docker-compose.yml)
#     rather than a separate beat service — fine for this v1 single-worker-
#     replica deployment (see reconcile_orphaned_jobs above for the same
#     assumption elsewhere), but would double-schedule if ever scaled to
#     multiple worker replicas.
app.conf.beat_schedule = {
    'renew-ssl-certs': {
        'task': 'sites.tasks.renew_ssl_certs_task',
        'schedule': crontab(hour=3, minute=30),
    },
}


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
