import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Site } from '../api/types';
import StatusBadge from '../components/StatusBadge';

export default function Dashboard() {
  const [sites, setSites] = useState<Site[] | null>(null);

  useEffect(() => {
    api
      .get<Site[]>('/sites/')
      .then(setSites)
      .catch(() => setSites([]));
  }, []);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Sites</h1>
        <Link
          to="/sites/new"
          className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400"
        >
          New site
        </Link>
      </div>

      {sites === null && <p className="text-slate-400">Loading...</p>}
      {sites?.length === 0 && (
        <p className="text-slate-400">No sites yet. Create a bench first, then a site.</p>
      )}

      <div className="grid gap-3">
        {sites?.map((site) => (
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
      </div>
    </div>
  );
}
