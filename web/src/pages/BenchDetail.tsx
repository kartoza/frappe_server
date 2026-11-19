import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { App, Bench, Job, Site } from '../api/types';
import BranchPicker from '../components/BranchPicker';
import JobLogViewer from '../components/JobLogViewer';
import StatusBadge from '../components/StatusBadge';

export default function BenchDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [bench, setBench] = useState<Bench | null>(null);
  const [sites, setSites] = useState<Site[]>([]);
  const [catalog, setCatalog] = useState<App[]>([]);
  const [latestJobId, setLatestJobId] = useState<number | null>(null);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [error, setError] = useState('');

  // Fetch/update both require picking a branch first — this is per-app
  // state for whichever app's picker is currently open.
  const [expandedAppId, setExpandedAppId] = useState<number | null>(null);
  const [appBranches, setAppBranches] = useState<Record<number, string[]>>({});
  const [selectedBranch, setSelectedBranch] = useState<Record<number, string>>({});

  function refreshBench() {
    api.get<Bench>(`/benches/${id}/`).then((data) => {
      setBench(data);
      if (data.status === 'creating' || data.status === 'deleting') {
        loadLatestJob();
      }
    });
  }

  function refreshSites() {
    api.get<Site[]>(`/sites/?bench=${id}`).then(setSites);
  }

  function refreshCatalog() {
    api.get<App[]>('/apps/').then(setCatalog);
  }

  async function loadLatestJob() {
    const jobs = await api.get<Job[]>(`/jobs/?bench=${id}`);
    if (jobs.length > 0) setLatestJobId(jobs[0].id);
  }

  useEffect(() => {
    refreshBench();
    refreshSites();
    refreshCatalog();
    loadLatestJob();
  }, [id]);

  async function handleDeleteBench() {
    if (!window.confirm('Delete this bench? This removes its containers and data permanently.')) {
      return;
    }
    try {
      await api.delete(`/benches/${id}/`);
      navigate('/benches');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete bench.');
    }
  }

  async function handleToggleExpand(app: App, currentBranch: string) {
    if (expandedAppId === app.id) {
      setExpandedAppId(null);
      return;
    }
    setExpandedAppId(app.id);
    setSelectedBranch((prev) => ({ ...prev, [app.id]: currentBranch || prev[app.id] || '' }));
    if (appBranches[app.id]) return;

    setError('');
    try {
      const result = await api.get<{ branches: string[] }>(
        `/apps/branches/?repo_url=${encodeURIComponent(app.repo_url)}`
      );
      setAppBranches((prev) => ({ ...prev, [app.id]: result.branches }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not fetch branches.');
    }
  }

  async function handleFetchApp(app: App) {
    const branch = selectedBranch[app.id];
    if (!branch) {
      setError('Choose a branch first.');
      return;
    }
    setError('');
    try {
      const result = await api.post<{ job_id: number }>(`/benches/${id}/fetch-app/`, {
        app_id: app.id,
        branch,
      });
      setActiveJobId(result.job_id);
      setExpandedAppId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not fetch app.');
    }
  }

  async function handleRemoveApp(app: App) {
    if (
      !window.confirm(
        `Remove ${app.name} from this bench entirely? This deletes its code bench-wide. ` +
          "Blocked if any site on this bench still has it installed."
      )
    ) {
      return;
    }
    setError('');
    try {
      const result = await api.post<{ job_id: number }>(`/benches/${id}/remove-app/`, {
        app_id: app.id,
      });
      setActiveJobId(result.job_id);
      setExpandedAppId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove app.');
    }
  }

  async function handleUpdateApp(app: App) {
    const branch = selectedBranch[app.id];
    if (!branch) {
      setError('Choose a branch first.');
      return;
    }
    const confirmed = window.confirm(
      `Pull ${app.name}@${branch} and migrate every site on this bench that has it installed. Continue?`
    );
    if (!confirmed) return;

    setError('');
    try {
      const result = await api.post<{ job_id: number; affected_sites: string[] }>(
        `/benches/${id}/update-app/`,
        { app_id: app.id, branch }
      );
      setActiveJobId(result.job_id);
      setExpandedAppId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update app.');
    }
  }

  if (!bench) return <p className="text-slate-400">Loading...</p>;

  const fetchedByAppId = new Map(bench.fetched_apps.map((fa) => [fa.app.id, fa]));

  return (
    <div>
      <div className="mb-2">
        <Link to="/benches" className="text-sm text-slate-400 hover:text-slate-200">
          &larr; Benches
        </Link>
      </div>

      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{bench.name}</h1>
          <div className="text-sm text-slate-400">{bench.frappe_branch}</div>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={bench.status} />
          <button
            onClick={handleDeleteBench}
            className="text-sm text-rose-400 hover:text-rose-300"
          >
            Delete bench
          </button>
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

      {(bench.status === 'creating' || bench.status === 'deleting') && latestJobId && (
        <div className="mb-8">
          <JobLogViewer jobId={latestJobId} onComplete={refreshBench} />
        </div>
      )}

      {activeJobId && (
        <div className="mb-8">
          <JobLogViewer
            jobId={activeJobId}
            onComplete={() => {
              refreshBench();
              refreshCatalog();
            }}
          />
        </div>
      )}

      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">Sites</h2>
          <Link
            to={`/benches/${id}/sites/new`}
            className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400"
          >
            + New site
          </Link>
        </div>
        <div className="grid gap-3">
          {sites.map((site) => (
            <Link
              key={site.id}
              to={`/sites/${site.id}`}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-slate-700"
            >
              <div>
                <div className="font-medium">{site.hostname}</div>
                <div className="text-sm text-slate-400">
                  {site.installed_apps.map((sa) => sa.app.name).join(', ') || 'No apps installed'}
                </div>
              </div>
              <StatusBadge status={site.status} />
            </Link>
          ))}
          {sites.length === 0 && (
            <p className="text-sm text-slate-400">No sites on this bench yet.</p>
          )}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">Apps</h2>
        <p className="mb-3 text-sm text-slate-500">
          Pick any app and any branch — there's no restriction tying an app's branch
          to this bench's own Frappe branch. &ldquo;Update&rdquo; re-pulls the chosen
          branch and migrates every site on this bench that has the app installed.
        </p>
        <ul className="space-y-2">
          {catalog.map((app) => {
            const fetched = fetchedByAppId.get(app.id);
            const expanded = expandedAppId === app.id;
            return (
              <li
                key={app.id}
                className="rounded-md border border-slate-800 bg-slate-900 px-4 py-3"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium">
                      {app.name}
                      {app.actual_name && app.actual_name !== app.name && (
                        <span className="ml-2 text-xs text-slate-500">
                          (registered as &ldquo;{app.actual_name}&rdquo;)
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-slate-500">
                      {fetched ? (
                        <span className="text-emerald-400">Fetched (branch: {fetched.branch})</span>
                      ) : (
                        'Not fetched'
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => handleToggleExpand(app, fetched?.branch ?? '')}
                      className="text-sm text-indigo-400 hover:underline"
                    >
                      {fetched ? 'Update' : 'Fetch to bench'}
                    </button>
                    {fetched && (
                      <button
                        onClick={() => handleRemoveApp(app)}
                        className="text-sm text-rose-400 hover:text-rose-300"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>

                {expanded && (
                  <div className="mt-3 border-t border-slate-800 pt-3">
                    <label className="mb-2 block text-sm text-slate-400">Branch</label>
                    {appBranches[app.id] ? (
                      <BranchPicker
                        branches={appBranches[app.id]}
                        value={selectedBranch[app.id] || ''}
                        onChange={(branch) =>
                          setSelectedBranch((prev) => ({ ...prev, [app.id]: branch }))
                        }
                      />
                    ) : (
                      <p className="text-sm text-slate-400">Loading branches...</p>
                    )}
                    <button
                      onClick={() => (fetched ? handleUpdateApp(app) : handleFetchApp(app))}
                      className="mt-3 rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400"
                    >
                      {fetched ? 'Update' : 'Fetch'}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
          {catalog.length === 0 && (
            <p className="text-sm text-slate-400">
              No apps in the catalog yet — add some under Settings.
            </p>
          )}
        </ul>
      </section>
    </div>
  );
}
