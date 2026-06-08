import { useEffect, useState } from "react";
import { fetchJobs, createJob, openInBlender, cancelJob } from "../api.js";

const QUALITIES = ["low", "high", "final"];

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
      {jobs.map((job) => (
        <li
          key={job.id}
          className={job.id === activeId ? "job active" : "job"}
          onClick={() => onSelect(job.id)}
        >
          <div className="job-row">
            <span className="job-name">
              #{job.id} {job.name}
            </span>
            <span className={`pill status-${job.status}`}>{job.status}</span>
          </div>
          <div className="job-meta">
            <code>{job.args.join(" ")}</code>
          </div>
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
              ⎘ use as template
            </button>
            {job.status === "success" && job.run_id != null && (
              <button
                className="link"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenRun(job.run_id);
                }}
              >
                → output{job.run_id} in gallery
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
      ))}
    </ul>
    </>
  );
}
