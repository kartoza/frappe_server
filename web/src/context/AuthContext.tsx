import { createContext, ReactNode, useContext, useEffect, useState } from 'react';
import {
  api,
  isAuthenticated,
  login as apiLogin,
  logout as apiLogout,
  storeTokens,
} from '../api/client';
import type { Me } from '../api/types';

interface AuthContextValue {
  authenticated: boolean;
  user: Me | null;
  login: (username: string, password: string) => Promise<void>;
  loginWithTokens: (access: string, refresh: string) => void;
  logout: () => void;
  refreshUser: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [user, setUser] = useState<Me | null>(null);

  function refreshUser() {
    if (!isAuthenticated()) {
      setUser(null);
      return;
    }
    api.get<Me>('/auth/me/').then(setUser).catch(() => setUser(null));
  }

  useEffect(refreshUser, [authenticated]);

  async function login(username: string, password: string) {
    await apiLogin(username, password);
    setAuthenticated(true);
  }

  function loginWithTokens(access: string, refresh: string) {
    storeTokens(access, refresh);
    setAuthenticated(true);
  }

  function logout() {
    apiLogout();
    setAuthenticated(false);
  }

  return (
    <AuthContext.Provider value={{ authenticated, user, login, loginWithTokens, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
