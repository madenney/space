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

export const thumbUrl = (id) => `/api/runs/${id}/thumb`;
export const frameUrl = (id, index) => `/api/runs/${id}/frames/${index}`;
export const videoUrl = (id) => `/api/runs/${id}/video`;

export async function fetchDefaults() {
  const res = await fetch("/api/config/defaults");
  if (!res.ok) throw new Error(`GET /api/config/defaults -> ${res.status}`);
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

export const jobLogUrl = (id) => `/api/jobs/${id}/logs`;
