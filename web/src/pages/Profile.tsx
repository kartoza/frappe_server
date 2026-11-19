import { FormEvent, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { useAuth } from '../context/AuthContext';

export default function Profile() {
  const { user, refreshUser } = useAuth();
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMessage, setProfileMessage] = useState('');
  const [profileError, setProfileError] = useState('');

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState('');
  const [passwordError, setPasswordError] = useState('');

  useEffect(() => {
    if (user) {
      setFirstName(user.first_name);
      setLastName(user.last_name);
    }
  }, [user]);

  async function handleProfileSubmit(event: FormEvent) {
    event.preventDefault();
    setProfileError('');
    setProfileMessage('');
    setProfileSaving(true);
    try {
      await api.patch('/auth/me/', { first_name: firstName, last_name: lastName });
      refreshUser();
      setProfileMessage('Profile updated.');
    } catch (err) {
      setProfileError(err instanceof ApiError ? err.message : 'Could not update profile.');
    } finally {
      setProfileSaving(false);
    }
  }

  async function handlePasswordSubmit(event: FormEvent) {
    event.preventDefault();
    setPasswordError('');
    setPasswordMessage('');
    if (newPassword !== confirmPassword) {
      setPasswordError('New passwords do not match.');
      return;
    }
    setPasswordSaving(true);
    try {
      await api.post('/auth/change-password/', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordMessage('Password updated.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : 'Could not update password.');
    } finally {
      setPasswordSaving(false);
    }
  }

  if (!user) return <p className="text-slate-400">Loading...</p>;

  return (
    <div className="max-w-xl">
      <h1 className="mb-6 text-2xl font-semibold">Profile</h1>

      <section className="mb-8 rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h2 className="mb-1 text-lg font-medium">Account</h2>
        <p className="mb-4 text-sm text-slate-400">
          {user.email} &middot; {user.is_admin ? 'Admin' : 'Member'}
        </p>

        <form onSubmit={handleProfileSubmit}>
          {profileError && <p className="mb-3 text-sm text-rose-400">{profileError}</p>}
          {profileMessage && <p className="mb-3 text-sm text-emerald-400">{profileMessage}</p>}
          <label className="mb-1 block text-sm text-slate-400">First name</label>
          <input
            className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
          />
          <label className="mb-1 block text-sm text-slate-400">Last name</label>
          <input
            className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
          />
          <button
            type="submit"
            disabled={profileSaving}
            className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {profileSaving ? 'Saving...' : 'Save'}
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h2 className="mb-4 text-lg font-medium">Reset password</h2>
        <form onSubmit={handlePasswordSubmit}>
          {passwordError && <p className="mb-3 text-sm text-rose-400">{passwordError}</p>}
          {passwordMessage && <p className="mb-3 text-sm text-emerald-400">{passwordMessage}</p>}
          <label className="mb-1 block text-sm text-slate-400">Current password</label>
          <input
            type="password"
            required
            className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <label className="mb-1 block text-sm text-slate-400">New password</label>
          <input
            type="password"
            required
            className="mb-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <label className="mb-1 block text-sm text-slate-400">Confirm new password</label>
          <input
            type="password"
            required
            className="mb-6 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          <button
            type="submit"
            disabled={passwordSaving}
            className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {passwordSaving ? 'Updating...' : 'Update password'}
          </button>
        </form>
      </section>
    </div>
  );
}
