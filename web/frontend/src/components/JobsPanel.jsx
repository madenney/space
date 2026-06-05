import { useEffect, useState } from "react";
import { fetchJobs } from "../api.js";

export default function JobsPanel({ activeId, onSelect, onOpenRun, refreshKey }) {
  const [jobs, setJobs] = useState([]);

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
          {job.error && <div className="job-err">{job.error}</div>}
        </li>
      ))}
    </ul>
  );
}
