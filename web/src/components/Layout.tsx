import { ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const NAV_ITEMS = [
  { to: '/', label: 'Sites' },
  { to: '/benches', label: 'Benches' },
  { to: '/settings', label: 'App catalog' },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { logout, user } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate('/login');
  }

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <aside className="w-56 shrink-0 border-r border-slate-800 bg-slate-900 p-4">
        <div className="mb-8 text-lg font-semibold tracking-tight">Frappe Platform</div>
        <nav className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
            >
              {item.label}
            </Link>
          ))}
          {user?.is_admin && (
            <Link
              to="/users"
              className="rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
            >
              Users
            </Link>
          )}
          <Link
            to="/profile"
            className="rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
          >
            Profile
          </Link>
        </nav>
        <button
          onClick={handleLogout}
          className="mt-8 w-full rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
        >
          Log out
        </button>
      </aside>
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}
