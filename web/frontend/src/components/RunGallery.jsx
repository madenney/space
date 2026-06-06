import { thumbUrl } from "../api.js";

function fmtDate(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function RunCard({ run, onOpen, onUseSettings }) {
  const rendered = run.frames_rendered > 0;
  return (
    <div className="card" onClick={() => onOpen(run.id)} role="button" tabIndex={0}>
      <div className="thumb">
        {rendered ? (
          <img src={thumbUrl(run.id)} alt={`run ${run.id} thumbnail`} loading="lazy" />
        ) : (
          <div className="thumb-empty">no frames</div>
        )}
        {run.has_video && <span className="badge video">▶ mp4</span>}
        <button
          className="badge use-badge"
          title="use these settings for a new render"
          onClick={(e) => {
            e.stopPropagation();
            onUseSettings(run.id);
          }}
        >
          ⎘ use
        </button>
      </div>
      <div className="card-body">
        <div className="card-title">
          <span className="run-name">{run.name}</span>
          {run.quality && <span className={`pill q-${run.quality}`}>{run.quality}</span>}
        </div>
        <div className="card-meta">
          <span>{run.body_count ?? "?"} bodies</span>
          <span>·</span>
          <span>
            {run.frames_rendered}/{run.frames_total ?? "?"} frames
          </span>
        </div>
        <div className="card-date">{fmtDate(run.modified_at)}</div>
      </div>
    </div>
  );
}

export default function RunGallery({ runs, loading, onOpen, onUseSettings }) {
  if (loading) return <div className="empty">loading runs…</div>;
  if (!runs.length) return <div className="empty">no runs in output/ yet</div>;
  return (
    <div className="gallery">
      {runs.map((run) => (
        <RunCard key={run.id} run={run} onOpen={onOpen} onUseSettings={onUseSettings} />
      ))}
    </div>
  );
}
