import { FormEvent, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { useAuth } from '../context/AuthContext';

interface InviteDetail {
  email: string;
  is_admin: boolean;
}

export default function AcceptInvite() {
  const { token } = useParams();
  const navigate = useNavigate();
  const { loginWithTokens } = useAuth();

  const [invite, setInvite] = useState<InviteDetail | null>(null);
  const [loadError, setLoadError] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .get<InviteDetail>(`/auth/invite/${token}/`)
      .then(setInvite)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Invite not found.'));
  }, [token]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setSubmitting(true);
    try {
      const result = await api.post<{ access: string; refresh: string }>(
        `/auth/invite/${token}/accept/`,
        { password }
      );
      loginWithTokens(result.access, result.refresh);
      navigate('/');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not accept invite.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950">
      <div className="w-full max-w-sm rounded-xl border border-slate-800 bg-slate-900 p-8">
        <h1 className="mb-6 text-xl font-semibold text-slate-100">Accept your invite</h1>

        {loadError && <p className="text-sm text-rose-400">{loadError}</p>}

        {!loadError && !invite && <p className="text-sm text-slate-400">Loading...</p>}

        {invite && (
          <form onSubmit={handleSubmit}>
            <p className="mb-4 text-sm text-slate-400">
              Setting a password for <span className="text-slate-200">{invite.email}</span>
              {invite.is_admin && ' (admin)'}
            </p>
            {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
            <label className="mb-1 block text-sm text-slate-400">Password</label>
            <input
              className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <label className="mb-1 block text-sm text-slate-400">Confirm password</label>
            <input
              className="mb-6 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              type="password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-indigo-500 px-3 py-2 font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              {submitting ? 'Creating account...' : 'Accept and sign in'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
