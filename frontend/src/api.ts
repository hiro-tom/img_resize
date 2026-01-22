export type SftpSettings = {
  host: string;
  port?: number;
  username: string;
  password?: string;
  private_key_path?: string;
  remote_dir: string;
  local_dir: string;
  interval_minutes?: number;
  compress_output_dir?: string;
  compress_quality?: number;
  remote_output_dir?: string;
  sync_interval_seconds?: number;
  compress_interval_seconds?: number;
  upload_interval_seconds?: number;
};

export type LogEntry = {
  id: number;
  created_at: string;
  level: string;
  message: string;
  detail?: string;
};

export type Status = {
  last_run?: string | null;
  next_run?: string | null;
  running: boolean;
};

const API_BASE = "http://localhost:8000";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || res.statusText);
  }
  return res.json();
}

export type CompressStatus = {
  running: boolean;
};

export type UploadStatus = {
  running: boolean;
};

export const api = {
  getSettings: () => jsonFetch<SftpSettings | null>("/settings"),
  saveSettings: (s: SftpSettings) =>
    jsonFetch<SftpSettings>("/settings", {
      method: "POST",
      body: JSON.stringify(s),
    }),
  triggerSync: (force = false) =>
    jsonFetch<{ status: string }>("/sync/run", {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  getLogs: (limit = 100) => jsonFetch<LogEntry[]>(`/logs?limit=${limit}`),
  getStatus: () => jsonFetch<Status>("/status"),
  triggerCompress: () =>
    jsonFetch<{ status: string }>("/compress/run", {
      method: "POST",
    }),
  stopCompress: () =>
    jsonFetch<{ status: string }>("/compress/stop", {
      method: "POST",
    }),
  getCompressStatus: () => jsonFetch<CompressStatus>("/compress/status"),
  triggerUpload: () =>
    jsonFetch<{ status: string }>("/upload/run", {
      method: "POST",
    }),
  stopUpload: () =>
    jsonFetch<{ status: string }>("/upload/stop", {
      method: "POST",
    }),
  getUploadStatus: () => jsonFetch<UploadStatus>("/upload/status"),
};
