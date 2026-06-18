import { useEffect, useState } from "react";
import { fetchJobs, createJob, openInBlender, cancelJob } from "../api.js";
import { overrideLabel } from "../fields.js";

const QUALITIES = ["low", "high", "final"];

// What kind of job is this, in plain words, inferred from the request.
function jobKind(req) {
  if (!req) return "render";
  if (req.prep_scene) return "prep scene";
  if (req.blender_scene) return req.first_frame ? "scene test" : "scene render";
  if (req.first_frame) return "test frame";
  return "render";
}

// One-line human summary of the params (vs. the raw CLI args).
function jobSummary(req) {
  if (!req) return "";
  const bits = [];
  if (req.quality) bits.push(req.quality);
  if (req.num_bodies != null) bits.push(`${req.num_bodies} bodies`);
  if (req.seconds != null) bits.push(`${req.seconds}s`);
  return bits.join(" · ");
}

// Friendly names for override keys come from the shared field registry.
function overrideTags(req) {
  const o = req?.config_override;
  if (!o) return [];
  return Object.keys(o).map(overrideLabel);
}

function relTime(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)}m ago`;
  const h = m / 60;
  if (h < 24) return `${Math.floor(h)}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function runDuration(a, b) {
  if (!a || !b) return null;
  const s = (new Date(b).getTime() - new Date(a).getTime()) / 1000;
  if (s < 1) return null;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return rs ? `${m}m ${rs}s` : `${m}m`;
}

export default function JobsPanel({ activeId, onSelect, onOpenRun, onRerun, onClone, refreshKey }) {
  const [jobs, setJobs] = useState([]);
  const [msg, setMsg] = useState(null);
  const [sceneQ, setSceneQ] = useState({}); // per-job quality for scene renders
  const qualityFor = (id) => sceneQ[id] || "final";

  async function rerun(job, e) {
    e.stopPropagation();
    const fresh = await createJob(job.request || {});
    onRerun?.(fresh);
  }

  async function cancel(job, e) {
    e.stopPropagation();
    setMsg(null);
    try {
      await cancelJob(job.id);
      setMsg(`cancelling #${job.id}…`);
      fetchJobs().then(setJobs).catch(() => {});
    } catch (err) {
      setMsg(`couldn't cancel: ${err.message}`);
    }
  }

  async function openBlender(job, e) {
    e.stopPropagation();
    setMsg(null);
    try {
      await openInBlender(job.run_id);
      setMsg(`opening output${job.run_id}/scene_edit.blend in Blender…`);
    } catch (err) {
      setMsg(`couldn't open Blender: ${err.message}`);
    }
  }

  async function renderScene(job, e) {
    e.stopPropagation();
    const fresh = await createJob({
      resume_run_id: job.run_id,
      blender_scene: job.scene_path,
      quality: qualityFor(job.id),
      name: `${job.name}-render`,
    });
    onRerun?.(fresh);
  }

  async function testFrame(job, e) {
    e.stopPropagation();
    const fresh = await createJob({
      resume_run_id: job.run_id,
      blender_scene: job.scene_path,
      first_frame: true,
      quality: qualityFor(job.id),
      name: `${job.name}-testframe`,
    });
    onRerun?.(fresh);
  }

  useEffect(() => {
    let alive = true;
    const load = () => fetchJobs().then((j) => alive && setJobs(j)).catch(() => {});
    load();
    // Poll while there's likely an active job; cheap enough for a solo local tool.
    const t = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [refreshKey]);

  if (!jobs.length) return <div className="empty small">no jobs yet</div>;

  return (
    <>
    {msg && <div className="notice">{msg}</div>}
    <ul className="jobs">
      {jobs.map((job) => {
        const tags = overrideTags(job.request);
        const summary = jobSummary(job.request);
        const dur = runDuration(job.started_at, job.finished_at);
        return (
        <li
          key={job.id}
          className={job.id === activeId ? "job active" : "job"}
          onClick={() => onSelect(job.id)}
        >
          {/* 1. header — id / name / status */}
          <div className="job-row">
            <span className="job-name">
              #{job.id}{job.name ? ` ${job.name}` : ""}
            </span>
            <span className={`pill status-${job.status}`}>{job.status}</span>
          </div>

          {/* 2. summary — kind badge + params */}
          <div className="job-summary">
            <span className="job-kind">{jobKind(job.request)}</span>
            {summary && <span className="job-params">{summary}</span>}
          </div>
          {tags.length > 0 && (
            <div className="job-tags">tweaked: {tags.join(", ")}</div>
          )}

          {/* 3. time — created / duration / output */}
          <div className="job-time">
            {relTime(job.created_at)}
            {dur && <> · ran {dur}</>}
            {job.run_id != null && <> · output{job.run_id}</>}
          </div>

          {/* 4. raw command, tucked away */}
          <details className="job-cli" onClick={(e) => e.stopPropagation()}>
            <summary>command</summary>
            <code>run.py {job.args.join(" ")}</code>
          </details>

          {/* 5. actions */}
          <div className="job-actions">
            {(job.status === "running" || job.status === "pending") && (
              <button className="link cancel" onClick={(e) => cancel(job, e)}>
                ✕ cancel
              </button>
            )}
            <button className="link" onClick={(e) => rerun(job, e)}>
              ↻ re-run
            </button>
            <button
              className="link"
              onClick={(e) => {
                e.stopPropagation();
                onClone?.(job.request || {});
              }}
            >
              ⎘ template
            </button>
            {job.status === "success" && job.run_id != null && (
              <button
                className="link"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenRun(job.run_id);
                }}
              >
                → gallery
              </button>
            )}
          </div>
          {job.scene_path && job.status === "success" && (
            <div className="scene-box">
              <div className="scene-label">🎬 editable scene ready</div>
              <div className="scene-actions">
                <button className="link" onClick={(e) => openBlender(job, e)}>
                  open in Blender
                </button>
                <span className="scene-q" onClick={(e) => e.stopPropagation()}>
                  quality
                  <select
                    value={qualityFor(job.id)}
                    onChange={(e) =>
                      setSceneQ((m) => ({ ...m, [job.id]: e.target.value }))
                    }
                  >
                    {QUALITIES.map((q) => (
                      <option key={q} value={q}>
                        {q}
                      </option>
                    ))}
                  </select>
                </span>
              </div>
              <div className="scene-actions">
                <button className="link" onClick={(e) => testFrame(job, e)}>
                  🔍 test frame
                </button>
                <button className="link" onClick={(e) => renderScene(job, e)}>
                  render this scene →
                </button>
              </div>
              <code className="scene-path">{job.scene_path}</code>
            </div>
          )}
          {job.error && <div className="job-err">{job.error}</div>}
        </li>
        );
      })}
    </ul>
    </>
  );
}
