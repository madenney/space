import { useEffect, useState } from "react";
import { fetchRun, frameUrl, videoUrl } from "../api.js";

function Stat({ label, value }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value ?? "—"}</span>
    </div>
  );
}

export default function RunDetail({ id, onBack, onUseSettings }) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [tab, setTab] = useState("frames");

  useEffect(() => {
    let alive = true;
    fetchRun(id)
      .then((d) => {
        if (!alive) return;
        setRun(d);
        setFrameIdx(d.frame_indices?.[0] ?? 0);
        setTab(d.has_video ? "video" : "frames");
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id]);

  const frames = run?.frame_indices || [];
  const hasFrames = frames.length > 0;

  // z = previous frame, x = next frame (only while the frames tab is active).
  useEffect(() => {
    if (tab !== "frames" || !hasFrames) return;
    function onKey(e) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      const k = e.key.toLowerCase();
      if (k !== "z" && k !== "x") return;
      e.preventDefault();
      setFrameIdx((cur) => {
        const pos = Math.max(0, frames.indexOf(cur));
        const next = k === "z" ? pos - 1 : pos + 1;
        return frames[Math.min(frames.length - 1, Math.max(0, next))];
      });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, hasFrames, frames]);

  if (error) return <div className="banner error">{error}</div>;
  if (!run) return <div className="empty">loading run {id}…</div>;

  const sliderPos = Math.max(0, frames.indexOf(frameIdx));

  return (
    <div className="detail">
      <div className="detail-head">
        <button className="ghost" onClick={onBack}>
          ← gallery
        </button>
        <h2>{run.name}</h2>
        {run.quality && <span className={`pill q-${run.quality}`}>{run.quality}</span>}
        <button className="ghost use-settings" onClick={onUseSettings}>
          ⎘ use these settings for a new render
        </button>
      </div>

      <div className="detail-grid">
        <div className="viewer">
          <div className="tabs">
            {run.has_video && (
              <button
                className={tab === "video" ? "tab active" : "tab"}
                onClick={() => setTab("video")}
              >
                video
              </button>
            )}
            <button
              className={tab === "frames" ? "tab active" : "tab"}
              onClick={() => setTab("frames")}
              disabled={!hasFrames}
            >
              frames
            </button>
          </div>

          {tab === "video" && run.has_video && (
            <video className="media" src={videoUrl(id)} controls loop />
          )}

          {tab === "frames" &&
            (hasFrames ? (
              <>
                <img
                  className="media"
                  src={frameUrl(id, frameIdx)}
                  alt={`frame ${frameIdx}`}
                />
                <div className="scrubber">
                  <input
                    type="range"
                    min={0}
                    max={frames.length - 1}
                    value={sliderPos}
                    onChange={(e) => setFrameIdx(frames[Number(e.target.value)])}
                  />
                  <span className="frame-label">
                    frame {frameIdx} ({sliderPos + 1}/{frames.length})
                    <span className="keyhint"> · z/x to step</span>
                  </span>
                </div>
              </>
            ) : (
              <div className="thumb-empty tall">no rendered frames</div>
            ))}
        </div>

        <aside className="sidebar">
          <div className="stats">
            <Stat label="bodies" value={run.body_count} />
            <Stat label="frames" value={`${run.frames_rendered}/${run.frames_total ?? "?"}`} />
            <Stat label="fps" value={run.frame_rate} />
            <Stat label="duration" value={run.duration_seconds ? `${run.duration_seconds}s` : null} />
            <Stat label="seed" value={run.seed} />
          </div>

          <details className="raw" open>
            <summary>config_used.json</summary>
            <pre>{JSON.stringify(run.config_used, null, 2)}</pre>
          </details>
          <details className="raw">
            <summary>run_metadata.json</summary>
            <pre>{JSON.stringify(run.metadata, null, 2)}</pre>
          </details>
        </aside>
      </div>
    </div>
  );
}
