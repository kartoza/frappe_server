import { FormEvent, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { Invite, PlatformUser } from '../api/types';
import { useAuth } from '../context/AuthContext';

export default function Users() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState('');
  const [inviting, setInviting] = useState(false);
  const [newInviteLink, setNewInviteLink] = useState('');
  const [copied, setCopied] = useState(false);

  function refresh() {
    api.get<PlatformUser[]>('/auth/users/').then(setUsers);
    api.get<Invite[]>('/auth/invites/').then(setInvites);
  }

  useEffect(refresh, []);

  async function handleInvite(event: FormEvent) {
    event.preventDefault();
    setError('');
    setInviting(true);
    try {
      const result = await api.post<Invite & { invite_link: string }>('/auth/invites/', {
        email,
        is_admin: isAdmin,
      });
      setNewInviteLink(result.invite_link);
      setEmail('');
      setIsAdmin(false);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send invite.');
    } finally {
      setInviting(false);
    }
  }

  async function handleCopyLink(link: string) {
    await navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function handleRevokeInvite(invite: Invite) {
    if (!window.confirm(`Revoke the invite to ${invite.email}?`)) return;
    await api.delete(`/auth/invites/${invite.id}/`);
    refresh();
  }

  async function handleToggleStaff(targetUser: PlatformUser) {
    setError('');
    try {
      await api.patch(`/auth/users/${targetUser.id}/`, { is_staff: !targetUser.is_staff });
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update user.');
    }
  }

  async function handleToggleActive(targetUser: PlatformUser) {
    setError('');
    try {
      await api.patch(`/auth/users/${targetUser.id}/`, { is_active: !targetUser.is_active });
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update user.');
    }
  }

  async function handleDeleteUser(targetUser: PlatformUser) {
    if (!window.confirm(`Remove ${targetUser.email}? This cannot be undone.`)) return;
    setError('');
    try {
      await api.delete(`/auth/users/${targetUser.id}/`);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove user.');
    }
  }

  const pendingInvites = invites.filter((i) => i.status !== 'accepted');

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold">Users</h1>

      {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

      <section className="mb-8 rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h2 className="mb-3 text-lg font-medium">Invite a user</h2>
        <form onSubmit={handleInvite} className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-sm text-slate-400">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-64 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            />
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm text-slate-300">
            <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
            Admin
          </label>
          <button
            type="submit"
            disabled={inviting}
            className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {inviting ? 'Sending...' : 'Send invite'}
          </button>
        </form>

        {newInviteLink && (
          <div className="mt-4 rounded-md border border-slate-700 bg-slate-950 p-3">
            <p className="mb-2 text-xs text-slate-400">
              Invite link (an email was also attempted, but share this directly if that doesn&apos;t
              arrive):
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto text-xs text-indigo-300">{newInviteLink}</code>
              <button
                onClick={() => handleCopyLink(newInviteLink)}
                className="shrink-0 rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-200 hover:bg-slate-800"
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </div>
        )}
      </section>

      {pendingInvites.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-3 text-lg font-medium">Pending invites</h2>
          <ul className="space-y-2">
            {pendingInvites.map((invite) => (
              <li
                key={invite.id}
                className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-3"
              >
                <div>
                  <div className="text-sm font-medium">{invite.email}</div>
                  <div className="text-xs text-slate-500">
                    {invite.is_admin ? 'Admin' : 'Member'} &middot; invited by{' '}
                    {invite.invited_by_email || 'unknown'} &middot;{' '}
                    <span className={invite.status === 'expired' ? 'text-rose-400' : 'text-amber-400'}>
                      {invite.status}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => handleRevokeInvite(invite)}
                  className="text-sm text-rose-400 hover:text-rose-300"
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-lg font-medium">Members</h2>
        <ul className="space-y-2">
          {users.map((u) => (
            <li
              key={u.id}
              className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900 px-4 py-3"
            >
              <div>
                <div className="text-sm font-medium">
                  {u.first_name || u.last_name ? `${u.first_name} ${u.last_name}`.trim() : u.email}
                  {!u.is_active && <span className="ml-2 text-xs text-rose-400">(deactivated)</span>}
                </div>
                <div className="text-xs text-slate-500">{u.email}</div>
              </div>
              <div className="flex items-center gap-4">
                <span className="text-xs text-slate-400">{u.is_staff ? 'Admin' : 'Member'}</span>
                {u.id !== me?.id && (
                  <>
                    <button
                      onClick={() => handleToggleStaff(u)}
                      className="text-sm text-indigo-400 hover:underline"
                    >
                      {u.is_staff ? 'Revoke admin' : 'Make admin'}
                    </button>
                    <button
                      onClick={() => handleToggleActive(u)}
                      className="text-sm text-slate-300 hover:underline"
                    >
                      {u.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                    <button
                      onClick={() => handleDeleteUser(u)}
                      className="text-sm text-rose-400 hover:text-rose-300"
                    >
                      Remove
                    </button>
                  </>
                )}
                {u.id === me?.id && <span className="text-xs text-slate-500">You</span>}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
