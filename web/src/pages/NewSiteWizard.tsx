import { FormEvent, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Bench } from '../api/types';
import JobLogViewer from '../components/JobLogViewer';

export default function NewSiteWizard() {
  const navigate = useNavigate();
  // Present when launched from a bench's own page (/benches/:benchId/sites/new)
  // — in that case the bench is fixed, not chosen from a dropdown.
  const { benchId: lockedBenchId } = useParams();
  const [benches, setBenches] = useState<Bench[]>([]);
  const [benchId, setBenchId] = useState<number | ''>('');
  const [hostname, setHostname] = useState('');
  const [adminPassword, setAdminPassword] = useState('');
  const [selectedApps, setSelectedApps] = useState<number[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get<Bench[]>('/benches/').then((data) => {
      setBenches(data);
      if (lockedBenchId) {
        setBenchId(Number(lockedBenchId));
      } else {
        const active = data.find((b) => b.status === 'active');
        if (active) setBenchId(active.id);
      }
    });
  }, [lockedBenchId]);

  // Only apps already fetched onto the chosen bench are installable — a
  // site can't fetch a new app itself, that's a bench-level, explicit step.
  useEffect(() => {
    setSelectedApps([]);
  }, [benchId]);

  function toggleApp(id: number) {
    setSelectedApps((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (!benchId) {
      setError('Choose a bench.');
      return;
    }
    try {
      const result = await api.post<{ job_id: number }>('/sites/', {
        bench: benchId,
        hostname,
        admin_password: adminPassword,
        app_ids: selectedApps,
      });
      setJobId(result.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create site.');
    }
  }

  const selectedBench = benches.find((b) => b.id === benchId);
  const availableApps = selectedBench?.fetched_apps.map((fa) => fa.app) ?? [];

  return (
    <div className="max-w-xl">
      <h1 className="mb-6 text-2xl font-semibold">New site</h1>

      {!jobId && (
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-lg border border-slate-800 bg-slate-900 p-6"
        >
          <div>
            <label className="mb-1 block text-sm text-slate-400">Bench</label>
            {lockedBenchId ? (
              <div className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100">
                {selectedBench
                  ? `${selectedBench.name} (${selectedBench.frappe_branch})`
                  : 'Loading...'}
              </div>
            ) : (
              <select
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                value={benchId}
                onChange={(e) => setBenchId(Number(e.target.value))}
                required
              >
                <option value="" disabled>
                  Select a bench...
                </option>
                {benches
                  .filter((b) => b.status === 'active')
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name} ({b.frappe_branch})
                    </option>
                  ))}
              </select>
            )}
          </div>

          <div>
            <label className="mb-1 block text-sm text-slate-400">Site hostname</label>
            <input
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              placeholder="demo.localhost"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-sm text-slate-400">Admin password</label>
            <input
              type="password"
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              required
            />
          </div>

          <div>
            <label className="mb-2 block text-sm text-slate-400">Apps to install</label>
            <div className="space-y-2">
              {availableApps.map((app) => (
                <label key={app.id} className="flex items-center gap-2 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    checked={selectedApps.includes(app.id)}
                    onChange={() => toggleApp(app.id)}
                  />
                  {app.name}
                </label>
              ))}
              {availableApps.length === 0 && (
                <p className="text-sm text-slate-500">
                  {benchId
                    ? 'No apps fetched on this bench yet — fetch some from the bench page first.'
                    : 'Choose a bench to see its fetched apps.'}
                </p>
              )}
            </div>
          </div>

          {error && <p className="text-sm text-rose-400">{error}</p>}

          <button
            type="submit"
            className="w-full rounded-md bg-indigo-500 px-4 py-2 font-medium text-white hover:bg-indigo-400"
          >
            Create site
          </button>
        </form>
      )}

      {jobId && (
        <div className="space-y-4">
          <JobLogViewer jobId={jobId} />
          <button
            onClick={() => navigate(lockedBenchId ? `/benches/${lockedBenchId}` : '/')}
            className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800"
          >
            {lockedBenchId ? 'Back to bench' : 'Back to sites'}
          </button>
        </div>
      )}
    </div>
  );
}
