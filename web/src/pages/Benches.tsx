import { FormEvent, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Bench } from '../api/types';
import StatusBadge from '../components/StatusBadge';

export default function Benches() {
  const navigate = useNavigate();
  const [benches, setBenches] = useState<Bench[] | null>(null);
  const [name, setName] = useState('');
  const [branch, setBranch] = useState('version-15');
  const [error, setError] = useState('');

  function refresh() {
    api
      .get<Bench[]>('/benches/')
      .then(setBenches)
      .catch(() => setBenches([]));
  }

  useEffect(refresh, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError('');
    try {
      // Jump straight to the detail page — it already shows live job
      // output (see JobLogViewer) while the bench's status is "creating".
      const bench = await api.post<Bench>('/benches/', { name, frappe_branch: branch });
      setName('');
      navigate(`/benches/${bench.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create bench.');
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold">Benches</h1>

      <form
        onSubmit={handleCreate}
        className="mb-8 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4"
      >
        <div>
          <label className="mb-1 block text-sm text-slate-400">Name</label>
          <input
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-400">Frappe branch</label>
          <select
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
          >
            <option value="version-16">version-16</option>
            <option value="version-15">version-15</option>
            <option value="version-14">version-14</option>
            <option value="develop">develop</option>
          </select>
        </div>
        <button
          type="submit"
          className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400"
        >
          Create bench
        </button>
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </form>

      <div className="grid gap-3">
        {benches?.map((bench) => (
          <Link
            key={bench.id}
            to={`/benches/${bench.id}`}
            className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-slate-700"
          >
            <div>
              <div className="font-medium">{bench.name}</div>
              <div className="text-sm text-slate-400">{bench.frappe_branch}</div>
            </div>
            <StatusBadge status={bench.status} />
          </Link>
        ))}
        {benches?.length === 0 && (
          <p className="text-sm text-slate-400">No benches yet. Create one above.</p>
        )}
      </div>
    </div>
  );
}
