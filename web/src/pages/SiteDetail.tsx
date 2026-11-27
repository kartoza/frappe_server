import { FormEvent, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, downloadFile, uploadFileChunked, type UploadProgress } from '../api/client';
import type { App, Backup, Bench, Job, Site } from '../api/types';
import JobLogViewer from '../components/JobLogViewer';
import StatusBadge from '../components/StatusBadge';

type Tab = 'overview' | 'apps' | 'domains' | 'config' | 'backups' | 'jobs' | 'restore';

interface RestoreItem {
  key: string;
  label: string;
  accept: string;
  description: string;
  confirmMessage?: string;
}

const RESTORE_ITEMS: RestoreItem[] = [
  {
    key: 'restore-database',
    label: 'Database',
    accept: '.sql,.sql.gz',
    description: 'Upload a .sql or .sql.gz dump to replace this site’s database.',
    confirmMessage: 'This replaces the site’s current database. Continue?',
  },
  {
    key: 'restore-site-config',
    label: 'Site config',
    accept: '.json,application/json',
    description: 'Upload a site_config.json to replace this site’s config.',
  },
  {
    key: 'restore-public-files',
    label: 'Public files',
    accept: '.zip,.tar,application/zip,application/x-tar',
    description:
      'Upload a zip or tar (e.g. a bench backup’s *-files.tar) to extract into the public files folder. Files with matching names are overwritten; others are left alone.',
  },
  {
    key: 'restore-private-files',
    label: 'Private files',
    accept: '.zip,.tar,application/zip,application/x-tar',
    description:
      'Upload a zip or tar (e.g. a bench backup’s *-private-files.tar) to extract into the private files folder. Files with matching names are overwritten; others are left alone.',
  },
];

export default function SiteDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [site, setSite] = useState<Site | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [backups, setBackups] = useState<Backup[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [catalog, setCatalog] = useState<App[]>([]);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [restoreFiles, setRestoreFiles] = useState<Record<string, File | null>>({});
  const [restoreSubmitting, setRestoreSubmitting] = useState<string | null>(null);
  // Bytes sent so far for whichever item is currently submitting — null
  // before the upload starts/after it ends. Kept as raw loaded/total
  // (rather than a single rounded percentage) because a whole-percent
  // rounding hides the first ~20MB of progress on a multi-gigabyte
  // upload, which reads as "stuck at 0%" even though it's moving.
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [newDomain, setNewDomain] = useState('');
  const [allowedDomains, setAllowedDomains] = useState<string[]>([]);
  const [domainSubmitting, setDomainSubmitting] = useState(false);
  const [configText, setConfigText] = useState('');
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState('');
  const [configMessage, setConfigMessage] = useState('');

  function refreshSite() {
    api.get<Site>(`/sites/${id}/`).then(setSite);
  }

  useEffect(refreshSite, [id]);

  useEffect(() => {
    api.get<{ domains: string[] }>('/sites/allowed-domains/').then((data) =>
      setAllowedDomains(data.domains)
    );
  }, []);

  // Only apps already fetched onto this site's bench are installable — the
  // backend enforces this too, but filtering here avoids offering an
  // install that would just be rejected. Fetching onto the bench (with an
  // explicit branch choice) happens from the bench's own page.
  useEffect(() => {
    if (!site) return;
    api.get<Bench>(`/benches/${site.bench}/`).then((bench) => {
      setCatalog(bench.fetched_apps.map((fa) => fa.app));
    });
  }, [site?.bench]);

  useEffect(() => {
    if (tab === 'backups') {
      api.get<Backup[]>(`/backups/?site=${id}`).then(setBackups);
    }
    if (tab === 'jobs') {
      api.get<Job[]>(`/jobs/?site=${id}`).then(setJobs);
    }
    if (tab === 'config') {
      loadConfig();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, id]);

  async function loadConfig() {
    setConfigError('');
    setConfigMessage('');
    setConfigLoading(true);
    try {
      const config = await api.get<Record<string, unknown>>(`/sites/${id}/site-config/`);
      setConfigText(JSON.stringify(config, null, 1));
    } catch (err) {
      setConfigError(err instanceof ApiError ? err.message : 'Could not load site_config.json.');
    } finally {
      setConfigLoading(false);
    }
  }

  async function handleSaveConfig() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(configText);
    } catch {
      setConfigError('Not valid JSON.');
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setConfigError('Must be a JSON object.');
      return;
    }
    if (
      !window.confirm(
        'Overwrite this site’s site_config.json with the edited content? This takes effect immediately.'
      )
    ) {
      return;
    }
    setConfigError('');
    setConfigMessage('');
    setConfigSaving(true);
    try {
      await api.put(`/sites/${id}/site-config/`, parsed);
      setConfigMessage('Saved.');
    } catch (err) {
      setConfigError(err instanceof ApiError ? err.message : 'Could not save site_config.json.');
    } finally {
      setConfigSaving(false);
    }
  }

  async function handleBackup() {
    const result = await api.post<{ job_id: number }>(`/sites/${id}/backup/`);
    setActiveJobId(result.job_id);
  }

  async function handleMigrate() {
    const result = await api.post<{ job_id: number }>(`/sites/${id}/migrate/`);
    setActiveJobId(result.job_id);
  }

  async function handleToggleMaintenance() {
    if (!site) return;
    const result = await api.post<{ job_id: number }>(`/sites/${id}/maintenance/`, {
      enabled: !site.maintenance_mode,
    });
    setActiveJobId(result.job_id);
    refreshSite();
  }

  async function handleAddDomain(event: FormEvent) {
    event.preventDefault();
    if (!newDomain.trim()) return;
    setError('');
    setDomainSubmitting(true);
    try {
      await api.post('/site-domains/', { site: id, domain: newDomain.trim() });
      setNewDomain('');
      refreshSite();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add domain.');
    } finally {
      setDomainSubmitting(false);
    }
  }

  async function handleRemoveDomain(domainId: number, domain: string) {
    if (!window.confirm(`Remove ${domain} from this site?`)) return;
    setError('');
    try {
      await api.delete(`/site-domains/${domainId}/`);
      refreshSite();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove domain.');
    }
  }

  async function handleSetDefaultDomain(domain: string) {
    setError('');
    try {
      await api.post(`/sites/${id}/set-default-domain/`, { domain });
      refreshSite();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not set default domain.');
    }
  }

  async function handleDeleteSite() {
    if (!site) return;
    if (
      !window.confirm(
        `Delete ${site.hostname}? This permanently drops its database and files.`
      )
    ) {
      return;
    }
    setError('');
    try {
      await api.delete(`/sites/${id}/`);
      navigate(`/benches/${site.bench}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete site.');
    }
  }

  async function handleInstallApp(appId: number) {
    setError('');
    try {
      const result = await api.post<{ job_id: number }>(`/sites/${id}/install-app/`, {
        app_id: appId,
      });
      setActiveJobId(result.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not install app.');
    }
  }

  async function handleUninstallApp(appName: string, appId: number) {
    if (
      !window.confirm(
        `Uninstall ${appName} from this site? This deletes that app's data from this site's database.`
      )
    ) {
      return;
    }
    setError('');
    try {
      const result = await api.post<{ job_id: number }>(`/sites/${id}/uninstall-app/`, {
        app_id: appId,
      });
      setActiveJobId(result.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not uninstall app.');
    }
  }

  async function handleDownload(backup: Backup) {
    await downloadFile(`/backups/${backup.id}/download/`, `${backup.file_path}.tar`);
  }

  async function handleDeleteBackup(backup: Backup) {
    if (
      !window.confirm(
        `Delete the backup taken ${new Date(backup.created_at).toLocaleString()}? This cannot be undone.`
      )
    ) {
      return;
    }
    setError('');
    try {
      await api.delete(`/backups/${backup.id}/`);
      setBackups((prev) => prev.filter((b) => b.id !== backup.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete backup.');
    }
  }

  async function handleRestoreFromBackup(backup: Backup) {
    if (
      !window.confirm(
        `Restore ${site?.hostname} from the backup taken ${new Date(backup.created_at).toLocaleString()}? This replaces the site's current database and files.`
      )
    ) {
      return;
    }
    setError('');
    try {
      const result = await api.post<{ job_id: number }>(`/sites/${id}/restore/`, {
        backup_id: backup.id,
      });
      setActiveJobId(result.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not restore from backup.');
    }
  }

  async function handleRestoreUpload(item: RestoreItem) {
    const file = restoreFiles[item.key];
    if (!file) return;
    if (item.confirmMessage && !window.confirm(item.confirmMessage)) return;

    setError('');
    setRestoreSubmitting(item.key);
    setUploadProgress({ loaded: 0, total: file.size });
    try {
      const result = await uploadFileChunked(id!, item.key, file, setUploadProgress);
      setActiveJobId(result.job_id);
      setRestoreFiles((prev) => ({ ...prev, [item.key]: null }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Restore failed.');
    } finally {
      setRestoreSubmitting(null);
      setUploadProgress(null);
    }
  }

  if (!site) return <p className="text-slate-400">Loading...</p>;

  const installedIds = new Set(site.installed_apps.map((sa) => sa.app.id));
  const availableApps = catalog.filter((app) => !installedIds.has(app.id));

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{site.hostname}</h1>
          {site.default_domain !== site.hostname && (
            <div className="text-sm text-slate-500">Serving as {site.default_domain}</div>
          )}
          <a
            href={`http://${site.default_domain}/`}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-indigo-400 hover:underline"
          >
            Open site &rarr;
          </a>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={site.status} />
          <button
            onClick={handleDeleteSite}
            className="text-sm text-rose-400 hover:text-rose-300"
          >
            Delete site
          </button>
        </div>
      </div>

      <div className="mb-6 flex gap-2 border-b border-slate-800">
        {(['overview', 'apps', 'domains', 'config', 'backups', 'restore', 'jobs'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm capitalize ${
              tab === t ? 'border-b-2 border-indigo-400 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

      {activeJobId && (
        <div className="mb-6">
          <JobLogViewer jobId={activeJobId} onComplete={refreshSite} />
        </div>
      )}

      {tab === 'overview' && (
        <div className="space-y-4 rounded-lg border border-slate-800 bg-slate-900 p-6">
          <div className="flex gap-3">
            <button
              onClick={handleBackup}
              className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400"
            >
              Back up now
            </button>
            <button
              onClick={handleMigrate}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800"
            >
              Run migrations
            </button>
            <button
              onClick={handleToggleMaintenance}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800"
            >
              {site.maintenance_mode ? 'Disable' : 'Enable'} maintenance mode
            </button>
          </div>
          <p className="text-sm text-slate-400">
            &ldquo;Maintenance mode&rdquo; takes the place of stop/start: a bench container
            hosts multiple sites, so there is no per-site container to stop.
          </p>
        </div>
      )}

      {tab === 'apps' && (
        <div className="space-y-6">
          <div>
            <h2 className="mb-2 text-sm font-medium text-slate-400">Installed</h2>
            <ul className="space-y-2">
              {site.installed_apps.map((sa) => (
                <li
                  key={sa.id}
                  className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-2"
                >
                  {sa.app.name}
                  <button
                    onClick={() => handleUninstallApp(sa.app.name, sa.app.id)}
                    className="text-sm text-rose-400 hover:text-rose-300"
                  >
                    Uninstall
                  </button>
                </li>
              ))}
              {site.installed_apps.length === 0 && (
                <p className="text-sm text-slate-500">No apps installed.</p>
              )}
            </ul>
          </div>
          {availableApps.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-medium text-slate-400">Available</h2>
              <ul className="space-y-2">
                {availableApps.map((app) => (
                  <li
                    key={app.id}
                    className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-2"
                  >
                    {app.name}
                    <button
                      onClick={() => handleInstallApp(app.id)}
                      className="text-sm text-indigo-400 hover:underline"
                    >
                      Install
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === 'domains' && (
        <div className="space-y-6">
          <div>
            <h2 className="mb-2 text-sm font-medium text-slate-400">Domains</h2>
            <p className="mb-3 text-xs text-slate-500">
              The default domain is the one that actually serves this site; every other
              domain redirects there.
            </p>
            <ul className="space-y-2">
              <li className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-2">
                <span>
                  {site.hostname}
                  <span className="ml-2 text-xs text-slate-500">(site identity, can&apos;t be removed)</span>
                </span>
                {site.default_domain === site.hostname ? (
                  <span className="text-xs text-emerald-400">Default</span>
                ) : (
                  <button
                    onClick={() => handleSetDefaultDomain(site.hostname)}
                    className="text-sm text-indigo-400 hover:underline"
                  >
                    Set as default
                  </button>
                )}
              </li>
              {site.domains.map((d) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-2"
                >
                  {d.domain}
                  <div className="flex items-center gap-3">
                    {d.is_default ? (
                      <span className="text-xs text-emerald-400">Default</span>
                    ) : (
                      <button
                        onClick={() => handleSetDefaultDomain(d.domain)}
                        className="text-sm text-indigo-400 hover:underline"
                      >
                        Set as default
                      </button>
                    )}
                    <button
                      onClick={() => handleRemoveDomain(d.id, d.domain)}
                      className="text-sm text-rose-400 hover:text-rose-300"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-slate-400">Add a domain</h2>
            <form onSubmit={handleAddDomain} className="flex items-end gap-3">
              <div>
                <input
                  className="w-64 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                  placeholder="shop.kartoza.com"
                  value={newDomain}
                  onChange={(e) => setNewDomain(e.target.value)}
                  required
                />
                {allowedDomains.length > 0 && (
                  <p className="mt-1 text-xs text-slate-500">
                    Must be, or be a subdomain of: {allowedDomains.join(', ')}
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={domainSubmitting}
                className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                {domainSubmitting ? 'Adding...' : 'Add domain'}
              </button>
            </form>
          </div>
        </div>
      )}

      {tab === 'config' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm text-slate-500">
              Edit this site&apos;s site_config.json directly. Saving overwrites the file
              immediately — there is no undo beyond restoring from a backup.
            </p>
            <button
              onClick={loadConfig}
              disabled={configLoading}
              className="shrink-0 rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              Reload
            </button>
          </div>

          {configError && <p className="mb-3 text-sm text-rose-400">{configError}</p>}
          {configMessage && <p className="mb-3 text-sm text-emerald-400">{configMessage}</p>}

          {configLoading ? (
            <p className="text-sm text-slate-400">Loading...</p>
          ) : (
            <>
              <textarea
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-slate-100"
                rows={16}
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                spellCheck={false}
              />
              <button
                onClick={handleSaveConfig}
                disabled={configSaving}
                className="mt-3 rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                {configSaving ? 'Saving...' : 'Save'}
              </button>
            </>
          )}
        </div>
      )}

      {tab === 'backups' && (
        <ul className="space-y-2">
          {backups.map((backup) => (
            <li
              key={backup.id}
              className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-3"
            >
              <div>
                <div className="text-sm">{backup.file_path || 'In progress...'}</div>
                <div className="text-xs text-slate-500">
                  {new Date(backup.created_at).toLocaleString()}
                  {backup.size_bytes ? ` · ${(backup.size_bytes / 1024 / 1024).toFixed(1)} MB` : ''}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge status={backup.status} />
                {backup.status === 'success' && (
                  <>
                    <button
                      onClick={() => handleDownload(backup)}
                      className="text-sm text-indigo-400 hover:underline"
                    >
                      Download
                    </button>
                    <button
                      onClick={() => handleRestoreFromBackup(backup)}
                      className="text-sm text-rose-400 hover:text-rose-300"
                    >
                      Restore
                    </button>
                  </>
                )}
                <button
                  onClick={() => handleDeleteBackup(backup)}
                  className="text-sm text-slate-400 hover:text-rose-300"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
          {backups.length === 0 && <p className="text-slate-400">No backups yet.</p>}
        </ul>
      )}

      {tab === 'restore' && (
        <div className="space-y-4">
          <p className="text-sm text-slate-500">
            Restore from files you provide directly, independent of this site&apos;s
            tracked backups above.
          </p>
          {RESTORE_ITEMS.map((item) => (
            <div key={item.key} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <div className="mb-1 text-sm font-medium">{item.label}</div>
              <p className="mb-3 text-xs text-slate-500">{item.description}</p>
              <div className="flex items-center gap-3">
                <input
                  type="file"
                  accept={item.accept}
                  onChange={(e) =>
                    setRestoreFiles((prev) => ({
                      ...prev,
                      [item.key]: e.target.files?.[0] ?? null,
                    }))
                  }
                  className="text-sm text-slate-300"
                />
                <button
                  onClick={() => handleRestoreUpload(item)}
                  disabled={!restoreFiles[item.key] || restoreSubmitting === item.key}
                  className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {restoreSubmitting === item.key ? 'Uploading...' : 'Restore'}
                </button>
              </div>
              {restoreSubmitting === item.key && uploadProgress !== null && (
                <div className="mt-3">
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-[width]"
                      style={{
                        width: `${Math.min(100, (uploadProgress.loaded / uploadProgress.total) * 100)}%`,
                      }}
                    />
                  </div>
                  {/* One decimal place, plus the raw MB count — a whole-percent
                      rounding hides all progress for the first ~20MB of a
                      multi-gigabyte upload, which reads as stuck even when
                      it isn't. */}
                  <div className="mt-1 text-xs text-slate-500">
                    {((uploadProgress.loaded / uploadProgress.total) * 100).toFixed(1)}% uploaded (
                    {(uploadProgress.loaded / 1024 / 1024).toFixed(1)} MB / {(uploadProgress.total / 1024 / 1024).toFixed(1)} MB)
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === 'jobs' && (
        <ul className="space-y-2">
          {jobs.map((job) => (
            <li key={job.id} className="rounded-md border border-slate-800 bg-slate-900 px-4 py-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm">{job.job_type}</span>
                <StatusBadge status={job.status} />
              </div>
              <div className="text-xs text-slate-500">{new Date(job.created_at).toLocaleString()}</div>
            </li>
          ))}
          {jobs.length === 0 && <p className="text-slate-400">No jobs yet.</p>}
        </ul>
      )}
    </div>
  );
}
