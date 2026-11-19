import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Job } from '../api/types';
import StatusBadge from './StatusBadge';

interface JobLogViewerProps {
  jobId: number;
  // Called once, when the job reaches a terminal state, so the parent page
  // can refresh anything derived from the job's outcome (e.g. a bench's
  // fetched-apps list after an app-fetch job finishes).
  onComplete?: (job: Job) => void;
}

export default function JobLogViewer({ jobId, onComplete }: JobLogViewerProps) {
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const data = await api.get<Job>(`/jobs/${jobId}/`);
        if (!active) return;
        setJob(data);
        if (data.status === 'queued' || data.status === 'running') {
          timer = setTimeout(poll, 2000);
        } else {
          onComplete?.(data);
        }
      } catch {
        // Stop polling silently on error; the viewer just stops updating.
      }
    }

    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
    // onComplete is deliberately not a dependency: it should only drive
    // re-polling when the job being watched changes, not when the parent
    // re-renders with a new callback identity.
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!job) return <p className="text-sm text-slate-400">Loading job...</p>;

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{job.job_type}</span>
        <StatusBadge status={job.status} />
      </div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-3 text-xs text-slate-300">
        {job.logs || 'Waiting for output...'}
        {job.log_truncated && '\n...output truncated...'}
      </pre>
    </div>
  );
}
