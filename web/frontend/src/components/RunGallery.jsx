import { thumbUrl } from "../api.js";

function fmtDate(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function RunCard({ run, onOpen }) {
  const rendered = run.frames_rendered > 0;
  return (
    <button className="card" onClick={() => onOpen(run.id)}>
      <div className="thumb">
        {rendered ? (
          <img src={thumbUrl(run.id)} alt={`run ${run.id} thumbnail`} loading="lazy" />
        ) : (
          <div className="thumb-empty">no frames</div>
        )}
        {run.has_video && <span className="badge video">▶ mp4</span>}
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
    </button>
  );
}

export default function RunGallery({ runs, loading, onOpen }) {
  if (loading) return <div className="empty">loading runs…</div>;
  if (!runs.length) return <div className="empty">no runs in output/ yet</div>;
  return (
    <div className="gallery">
      {runs.map((run) => (
        <RunCard key={run.id} run={run} onOpen={onOpen} />
      ))}
    </div>
  );
}
