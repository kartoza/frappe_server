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
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
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

// `fetch` has no reliable, widely-supported way to report upload progress —
// XMLHttpRequest's `upload.onprogress` is the only thing that actually
// works across browsers for this, hence the lower-level API here instead
// of building on `fetch`.
export interface UploadProgress {
  loaded: number;
  total: number;
}

export function postFormWithProgress<T>(
  path: string,
  formData: FormData,
  onProgress?: (progress: UploadProgress) => void
): Promise<T> {
  const tokens = getTokens();

  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api${path}`);
    if (tokens) xhr.setRequestHeader('Authorization', `Bearer ${tokens.access}`);

    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress({ loaded: event.loaded, total: event.total });
      }
    };
    xhr.onabort = () => reject(new ApiError(0, 'Upload aborted.'));
    xhr.ontimeout = () => reject(new ApiError(0, 'Upload timed out.'));

    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Non-JSON body (e.g. an empty response) — extractErrorMessage
        // below falls back to a generic message in that case.
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T);
      } else {
        reject(new ApiError(xhr.status, extractErrorMessage(body)));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload.'));

    xhr.send(formData);
  });
}

// Comfortably under Cloudflare's own upload-size cap, which sits in front
// of this deployment and enforces its own limit independent of anything
// configured on this server — a restore upload has to arrive as many
// small requests, since one multi-gigabyte request never arrives intact.
const RESTORE_CHUNK_SIZE = 20 * 1024 * 1024;

function generateUploadId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for non-secure contexts (plain http://, e.g. local dev)
  // where crypto.randomUUID isn't available — doesn't need to be
  // cryptographically strong, just distinct enough to namespace this
  // upload's chunk directory on the server.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Keyed by file identity (not just name) so retrying the exact same file
// resumes from whatever chunks already landed, but picking a different
// file — even one with the same name — starts a fresh upload rather than
// confusing the two.
function uploadIdStorageKey(file: File): string {
  return `restore-upload-id:${file.name}:${file.size}:${file.lastModified}`;
}

async function uploadChunkWithRetry(
  siteId: string,
  uploadId: string,
  chunkIndex: number,
  blob: Blob,
  onChunkProgress: (loaded: number) => void,
  attemptsLeft = 3
): Promise<void> {
  const formData = new FormData();
  formData.append('upload_id', uploadId);
  formData.append('chunk_index', String(chunkIndex));
  formData.append('chunk', blob, 'chunk');
  try {
    await postFormWithProgress(`/sites/${siteId}/upload-chunk/`, formData, (p) =>
      onChunkProgress(p.loaded)
    );
  } catch (err) {
    if (attemptsLeft <= 1) throw err;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await uploadChunkWithRetry(siteId, uploadId, chunkIndex, blob, onChunkProgress, attemptsLeft - 1);
  }
}

// Uploads `file` as a series of small chunks instead of one request (see
// RESTORE_CHUNK_SIZE), reassembled server-side by `/upload-complete/` into
// the ordinary restore action named by `restoreType`. Resumable: retrying
// with the same file skips whatever chunks the server already has.
export async function uploadFileChunked(
  siteId: string,
  restoreType: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<{ job_id: number }> {
  const storageKey = uploadIdStorageKey(file);
  let uploadId = localStorage.getItem(storageKey);
  if (!uploadId) {
    uploadId = generateUploadId();
    localStorage.setItem(storageKey, uploadId);
  }

  const totalChunks = Math.max(1, Math.ceil(file.size / RESTORE_CHUNK_SIZE));

  let received: Set<number>;
  try {
    const statusResult = await api.get<{ received: number[] }>(
      `/sites/${siteId}/upload-status/?upload_id=${uploadId}`
    );
    received = new Set(statusResult.received);
  } catch {
    received = new Set();
  }

  let completedBytes = 0;
  for (let i = 0; i < totalChunks; i++) {
    const start = i * RESTORE_CHUNK_SIZE;
    const end = Math.min(start + RESTORE_CHUNK_SIZE, file.size);
    const chunkSize = end - start;

    if (received.has(i)) {
      completedBytes += chunkSize;
      onProgress?.({ loaded: completedBytes, total: file.size });
      continue;
    }

    await uploadChunkWithRetry(siteId, uploadId, i, file.slice(start, end), (loadedInChunk) =>
      onProgress?.({ loaded: completedBytes + loadedInChunk, total: file.size })
    );
    completedBytes += chunkSize;
    onProgress?.({ loaded: completedBytes, total: file.size });
  }

  const result = await api.post<{ job_id: number }>(`/sites/${siteId}/upload-complete/`, {
    upload_id: uploadId,
    total_chunks: totalChunks,
    filename: file.name,
    restore_type: restoreType,
  });
  localStorage.removeItem(storageKey);
  return result;
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
