export type JobStatus = 'queued' | 'running' | 'success' | 'failed';
export type BenchStatus = 'creating' | 'active' | 'error' | 'deleting';
export type SiteStatus = 'creating' | 'active' | 'suspended' | 'error' | 'deleting';

export interface App {
  id: number;
  name: string;
  repo_url: string;
  // The real name bench registers this app under, discovered the first time
  // it's fetched anywhere — blank until then. Can differ from `name` (what
  // was typed into the catalog) since bench derives it from the repo's own
  // code, not the repo name.
  actual_name: string;
  created_at: string;
}

export interface BenchApp {
  id: number;
  app: App;
  // Branch fetched onto this bench — chosen explicitly by the operator
  // each time, not restricted or auto-derived from the bench's branch.
  branch: string;
  fetched_at: string;
}

export interface Bench {
  id: number;
  server: number;
  name: string;
  frappe_branch: string;
  status: BenchStatus;
  error_message: string;
  created_at: string;
  fetched_apps: BenchApp[];
}

export interface SiteApp {
  id: number;
  app: App;
  installed_at: string;
}

export interface Site {
  id: number;
  bench: number;
  hostname: string;
  status: SiteStatus;
  maintenance_mode: boolean;
  error_message: string;
  created_at: string;
  installed_apps: SiteApp[];
}

export interface Backup {
  id: number;
  site: number;
  file_path: string;
  size_bytes: number | null;
  status: 'running' | 'success' | 'failed';
  created_at: string;
}

export interface Me {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_admin: boolean;
}

export interface PlatformUser {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_staff: boolean;
  is_active: boolean;
  date_joined: string;
  last_login: string | null;
}

export type InviteStatus = 'pending' | 'expired' | 'accepted';

export interface Invite {
  id: number;
  email: string;
  is_admin: boolean;
  status: InviteStatus;
  invited_by_email: string;
  created_at: string;
  accepted_at: string | null;
}

export interface Job {
  id: number;
  job_type: string;
  bench: number | null;
  site: number | null;
  status: JobStatus;
  logs: string;
  log_truncated: boolean;
  created_at: string;
  finished_at: string | null;
}
