import { ReactElement } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { AuthProvider, useAuth } from './context/AuthContext';
import AcceptInvite from './pages/AcceptInvite';
import BenchDetail from './pages/BenchDetail';
import Benches from './pages/Benches';
import Dashboard from './pages/Dashboard';
import Login from './pages/Login';
import NewSiteWizard from './pages/NewSiteWizard';
import Profile from './pages/Profile';
import Settings from './pages/Settings';
import SiteDetail from './pages/SiteDetail';
import SqlPlayground from './pages/SqlPlayground';
import Users from './pages/Users';

function ProtectedRoute({ children }: { children: ReactElement }) {
  const { authenticated } = useAuth();
  if (!authenticated) return <Navigate to="/login" replace />;
  return children;
}

function AdminRoute({ children }: { children: ReactElement }) {
  const { user } = useAuth();
  if (user && !user.is_admin) return <Navigate to="/" replace />;
  return children;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/accept-invite/:token" element={<AcceptInvite />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/benches" element={<Benches />} />
                <Route path="/benches/:id" element={<BenchDetail />} />
                <Route path="/benches/:benchId/sites/new" element={<NewSiteWizard />} />
                <Route path="/sites/new" element={<NewSiteWizard />} />
                <Route path="/sites/:id" element={<SiteDetail />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/profile" element={<Profile />} />
                <Route
                  path="/users"
                  element={
                    <AdminRoute>
                      <Users />
                    </AdminRoute>
                  }
                />
                <Route
                  path="/sql"
                  element={
                    <AdminRoute>
                      <SqlPlayground />
                    </AdminRoute>
                  }
                />
              </Routes>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
