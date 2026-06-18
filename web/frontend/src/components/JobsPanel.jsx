import { useEffect, useState } from "react";
import {
  fetchJobs,
  createJob,
  openInBlender,
  cancelJob,
  deleteJob,
  thumbUrl,
  videoUrl,
} from "../api.js";
import { overrideLabel } from "../fields.js";

// Small thumbnail pinned to the left of the row; the click target that toggles
// the expanded video. Hides itself if the run produced no frames (prep job).
function JobThumb({ runId, open, onToggle }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    <button
      className={open ? "thumb-btn open" : "thumb-btn"}
      onClick={onToggle}
      title={open ? "Hide preview" : "Show preview"}
    >
      <img
        className="job-thumb"
        src={thumbUrl(runId)}
        alt=""
        loading="lazy"
        onError={() => setBroken(true)}
      />
      <span className="thumb-badge">{open ? "▾" : "▶"}</span>
    </button>
  );
}

// Expanded video (full row width); falls back to the still for single-frame runs.
function JobVideo({ runId, onCollapse, onOpenRun }) {
  const [noVideo, setNoVideo] = useState(false);
  return (
    <div className="preview-expanded" onClick={(e) => e.stopPropagation()}>
      {!noVideo ? (
        <video
          className="job-video"
          src={videoUrl(runId)}
          poster={thumbUrl(runId)}
          controls
          autoPlay
          loop
          muted
          onError={() => setNoVideo(true)}
        />
      ) : (
        <img className="job-still" src={thumbUrl(runId)} alt="" />
      )}
      <div className="preview-actions">
        <button className="link" onClick={onCollapse}>▴ collapse</button>
        <button className="link" onClick={onOpenRun}>↗ open in gallery</button>
      </div>
    </div>
  );
}

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
  const [openPreview, setOpenPreview] = useState({}); // job.id -> expanded?
  const togglePreview = (id, e) => {
    e.stopPropagation();
    setOpenPreview((m) => ({ ...m, [id]: !m[id] }));
  };
  const [confirmDel, setConfirmDel] = useState({}); // job.id -> awaiting confirm?
  const askDelete = (id, e) => {
    e.stopPropagation();
    setConfirmDel((m) => ({ ...m, [id]: true }));
  };
  const cancelDelete = (id, e) => {
    e.stopPropagation();
    setConfirmDel((m) => ({ ...m, [id]: false }));
  };

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

  async function removeJob(job, e) {
    e.stopPropagation();
    setMsg(null);
    try {
      await deleteJob(job.id);
      setMsg(`deleted #${job.id}`);
      fetchJobs().then(setJobs).catch(() => {});
    } catch (err) {
      setMsg(`couldn't delete: ${err.message}`);
    } finally {
      setConfirmDel((m) => ({ ...m, [job.id]: false }));
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
          {/* thumbnail (left) + body (right) */}
          <div className="job-main">
            {job.status === "success" && job.run_id != null && (
              <JobThumb
                runId={job.run_id}
                open={!!openPreview[job.id]}
                onToggle={(e) => togglePreview(job.id, e)}
              />
            )}
            <div className="job-body">
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
                {confirmDel[job.id] ? (
                  <span className="confirm-delete" onClick={(e) => e.stopPropagation()}>
                    delete?
                    <button className="link delete" onClick={(e) => removeJob(job, e)}>yes</button>
                    <button className="link" onClick={(e) => cancelDelete(job.id, e)}>no</button>
                  </span>
                ) : (
                  <button className="link delete" onClick={(e) => askDelete(job.id, e)}>
                    🗑 delete
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* expanded result video (full width, below the row) */}
          {job.status === "success" && job.run_id != null && openPreview[job.id] && (
            <JobVideo
              runId={job.run_id}
              onCollapse={(e) => togglePreview(job.id, e)}
              onOpenRun={() => onOpenRun(job.run_id)}
            />
          )}
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
