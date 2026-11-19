import { FormEvent, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { App } from '../api/types';

export default function Settings() {
  const [apps, setApps] = useState<App[]>([]);
  const [name, setName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [error, setError] = useState('');

  function refresh() {
    api.get<App[]>('/apps/').then(setApps);
  }

  useEffect(refresh, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError('');
    try {
      await api.post('/apps/', { name, repo_url: repoUrl });
      setName('');
      setRepoUrl('');
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add app.');
    }
  }

  async function handleDelete(id: number) {
    await api.delete(`/apps/${id}/`);
    refresh();
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold">App catalog</h1>

      <form
        onSubmit={handleCreate}
        className="mb-6 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4"
      >
        <div>
          <label className="mb-1 block text-sm text-slate-400">App name</label>
          <input
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            placeholder="erpnext"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-400">Repository URL</label>
          <input
            className="w-72 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            placeholder="https://github.com/frappe/erpnext"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            required
          />
        </div>
        <button
          type="submit"
          className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400"
        >
          Add app
        </button>
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </form>

      <p className="mb-4 text-sm text-slate-500">
        Which branch to fetch is chosen per bench, when you fetch or update the app
        there — not restricted here.
      </p>

      <ul className="space-y-2">
        {apps.map((app) => (
          <li
            key={app.id}
            className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-3"
          >
            <div>
              <div className="text-sm font-medium">{app.name}</div>
              <div className="text-xs text-slate-500">{app.repo_url}</div>
            </div>
            <button onClick={() => handleDelete(app.id)} className="text-sm text-rose-400 hover:text-rose-300">
              Remove
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
