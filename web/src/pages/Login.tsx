import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/');
    } catch {
      setError('Invalid email or password.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl border border-slate-800 bg-slate-900 p-8"
      >
        <h1 className="mb-6 text-xl font-semibold text-slate-100">Frappe Platform</h1>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        <label className="mb-1 block text-sm text-slate-400">Email</label>
        <input
          className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          type="email"
          required
        />
        <label className="mb-1 block text-sm text-slate-400">Password</label>
        <input
          className="mb-6 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          required
        />
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-indigo-500 px-3 py-2 font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
