const TOKEN_KEY = 'frappe_platform_tokens';

interface Tokens {
  access: string;
  refresh: string;
}

function getTokens(): Tokens | null {
  const raw = localStorage.getItem(TOKEN_KEY);
  return raw ? (JSON.parse(raw) as Tokens) : null;
}

function setTokens(tokens: Tokens | null) {
  if (tokens) {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// DRF error bodies come in a few shapes depending on where they're raised:
// `{"detail": "..."}` from action views, `{"non_field_errors": ["..."]}`
// from a serializer's object-level `validate()`, or `{"field": ["..."]}`
// from a single field's validation. Surface whichever one we got.
function extractErrorMessage(body: Record<string, unknown>): string {
  if (typeof body.detail === 'string') return body.detail;
  for (const value of Object.values(body)) {
    if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
  }
  return 'Request failed';
}

async function tryRefresh(refresh: string): Promise<boolean> {
  try {
    const response = await fetch('/api/auth/refresh/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh }),
    });
    if (!response.ok) return false;
    const data = await response.json();
    setTokens({ access: data.access, refresh });
    return true;
  } catch {
    return false;
  }
}

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const tokens = getTokens();
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (tokens) {
    headers.set('Authorization', `Bearer ${tokens.access}`);
  }

  const response = await fetch(`/api${path}`, { ...options, headers });

  if (response.status === 401 && tokens?.refresh && retry) {
    const refreshed = await tryRefresh(tokens.refresh);
    if (refreshed) {
      return request<T>(path, options, false);
    }
    setTokens(null);
    window.location.href = '/login';
    throw new ApiError(401, 'Session expired');
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, extractErrorMessage(body));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export async function login(username: string, password: string) {
  const response = await fetch('/api/auth/login/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, 'Invalid credentials');
  }
  const data = await response.json();
  setTokens({ access: data.access, refresh: data.refresh });
}

export function logout() {
  setTokens(null);
}

// Used after accepting an invite, which returns tokens directly from a
// non-login endpoint rather than going through `login()` above.
export function storeTokens(access: string, refresh: string) {
  setTokens({ access, refresh });
}

export function isAuthenticated(): boolean {
  return getTokens() !== null;
}

export async function postForm<T>(path: string, formData: FormData): Promise<T> {
  const tokens = getTokens();
  const headers = new Headers();
  if (tokens) headers.set('Authorization', `Bearer ${tokens.access}`);
  // No Content-Type here — fetch sets the multipart boundary itself when
  // the body is FormData; setting it manually would break the upload.

  const response = await fetch(`/api${path}`, { method: 'POST', headers, body: formData });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, extractErrorMessage(body));
  }
  return response.json();
}

export async function downloadFile(path: string, filename: string) {
  const tokens = getTokens();
  const headers = new Headers();
  if (tokens) headers.set('Authorization', `Bearer ${tokens.access}`);

  const response = await fetch(`/api${path}`, { headers });
  if (!response.ok) {
    throw new ApiError(response.status, 'Download failed');
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
