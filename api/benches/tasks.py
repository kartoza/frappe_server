"""Celery tasks for bench-unit lifecycle (create/delete)."""
import time

from celery import shared_task

from .docker_client import (
    create_bench_containers,
    remove_bench_containers,
    wait_for_bench_ready,
)
from .models import Bench


@shared_task
def create_bench_task(bench_id, job_id):
    """Create a bench's containers/volume and wait for it to come up.

    Rolls back anything already created if any step fails.
    """
    from sites.models import Job  # avoid a cross-app import cycle at module load

    bench = Bench.objects.get(pk=bench_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        job.append_log(
            f'Creating bench "{bench.name}" on branch {bench.frappe_branch}...\n'
        )
        since = time.time()
        create_bench_containers(bench)
        job.append_log('Waiting for bench to become ready...\n')
        wait_for_bench_ready(bench, on_output=job.append_log, since=since)
        bench.status = Bench.STATUS_ACTIVE
        bench.save(update_fields=['status'])
        job.append_log('Bench is active.\n')
        job.mark_success()
    except Exception as exc:  # noqa: BLE001 - must always resolve the job
        job.append_log(f'Bench creation failed: {exc}\n')
        try:
            remove_bench_containers(bench, remove_volume=True)
        except Exception as cleanup_exc:  # noqa: BLE001
            job.append_log(f'Cleanup after failure also failed: {cleanup_exc}\n')
        bench.status = Bench.STATUS_ERROR
        bench.error_message = str(exc)
        bench.save(update_fields=['status', 'error_message'])
        job.mark_failed(str(exc))


@shared_task
def delete_bench_task(bench_id, job_id):
    """Remove a bench's containers/volume and delete its DB row."""
    from sites.models import Job

    bench = Bench.objects.get(pk=bench_id)
    job = Job.objects.get(pk=job_id)
    job.mark_running()

    try:
        job.append_log(f'Deleting bench "{bench.name}"...\n')
        remove_bench_containers(bench, remove_volume=True)
        bench.delete()
        job.mark_success()
    except Exception as exc:  # noqa: BLE001
        job.append_log(f'Bench deletion failed: {exc}\n')
        bench.status = Bench.STATUS_ERROR
        bench.error_message = str(exc)
        bench.save(update_fields=['status', 'error_message'])
        job.mark_failed(str(exc))
