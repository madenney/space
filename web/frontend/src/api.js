// Thin API client. All paths are relative so the Vite proxy (dev) or a reverse
// proxy (remote) routes them to the backend.

export async function fetchRuns() {
  const res = await fetch("/api/runs");
  if (!res.ok) throw new Error(`GET /api/runs -> ${res.status}`);
  return res.json();
}

export async function fetchRun(id) {
  const res = await fetch(`/api/runs/${id}`);
  if (!res.ok) throw new Error(`GET /api/runs/${id} -> ${res.status}`);
  return res.json();
}

export async function fetchRunSpec(id) {
  const res = await fetch(`/api/runs/${id}/spec`);
  if (!res.ok) throw new Error(`GET /api/runs/${id}/spec -> ${res.status}`);
  return res.json();
}

export const thumbUrl = (id) => `/api/runs/${id}/thumb`;
export const frameUrl = (id, index) => `/api/runs/${id}/frames/${index}`;
export const videoUrl = (id) => `/api/runs/${id}/video`;

export async function fetchDefaults() {
  const res = await fetch("/api/config/defaults");
  if (!res.ok) throw new Error(`GET /api/config/defaults -> ${res.status}`);
  return res.json();
}

// Editable-field schema (the builder renders its form from this). Source of
// truth is config.py · FIELD_SCHEMA. Returns { fields, camera_move }.
export async function fetchFields() {
  const res = await fetch("/api/config/fields");
  if (!res.ok) throw new Error(`GET /api/config/fields -> ${res.status}`);
  return res.json();
}

export async function createJob(payload) {
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`POST /api/jobs -> ${res.status}`);
  return res.json();
}

export async function fetchJobs() {
  const res = await fetch("/api/jobs");
  if (!res.ok) throw new Error(`GET /api/jobs -> ${res.status}`);
  return res.json();
}

export async function openInBlender(run_id) {
  const res = await fetch("/api/blender/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `open failed -> ${res.status}`);
  }
  return res.json();
}

// Open the output folder (or one run's dir) in the host's file manager.
export async function openFolder(runId) {
  const res = await fetch("/api/open-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(runId != null ? { run_id: runId } : {}),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `open folder -> ${res.status}`);
  }
  return res.json();
}

export async function cancelJob(id) {
  const res = await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /api/jobs/${id}/cancel -> ${res.status}`);
  return res.json();
}

export async function deleteJob(id) {
  const res = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `DELETE /api/jobs/${id} -> ${res.status}`);
  }
  return res.json();
}

export const jobLogUrl = (id) => `/api/jobs/${id}/logs`;

export async function fetchPresets() {
  const res = await fetch("/api/presets");
  if (!res.ok) throw new Error(`GET /api/presets -> ${res.status}`);
  return res.json();
}

export async function savePreset(payload) {
  const res = await fetch("/api/presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`POST /api/presets -> ${res.status}`);
  return res.json();
}

export async function deletePreset(name) {
  const res = await fetch(`/api/presets/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`DELETE /api/presets/${name} -> ${res.status}`);
  return res.json();
}
