import { useEffect, useState } from "react";
import { fetchRuns } from "./api.js";
import RunGallery from "./components/RunGallery.jsx";
import RunDetail from "./components/RunDetail.jsx";
import JobBuilder from "./components/JobBuilder.jsx";
import JobsPanel from "./components/JobsPanel.jsx";
import LogConsole from "./components/LogConsole.jsx";

export default function App() {
  // Hash deep-links: #render or #render/<jobId>
  const initialHash = window.location.hash;
  const [view, setView] = useState(() =>
    initialHash.startsWith("#render") ? "render" : "gallery"
  ); // "gallery" | "render"
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [activeJob, setActiveJob] = useState(() => {
    const m = initialHash.match(/^#render\/(\d+)$/);
    return m ? Number(m[1]) : null;
  });
  const [jobsRefresh, setJobsRefresh] = useState(0);

  async function loadRuns() {
    setLoading(true);
    try {
      setRuns(await fetchRuns());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRuns();
  }, []);

  function openRun(id) {
    setSelectedRun(id);
    setView("gallery");
    loadRuns();
  }

  function onJobSubmitted(job) {
    setActiveJob(job.id);
    setJobsRefresh((n) => n + 1);
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1 className="logo" onClick={() => { setView("gallery"); setSelectedRun(null); }}>
          ◈ Sim / Render Studio
        </h1>
        <nav className="nav">
          <button
            className={view === "gallery" ? "nav-btn active" : "nav-btn"}
            onClick={() => setView("gallery")}
          >
            gallery
          </button>
          <button
            className={view === "render" ? "nav-btn active" : "nav-btn"}
            onClick={() => setView("render")}
          >
            render
          </button>
        </nav>
        <span className="phase-tag">phase 1</span>
        {view === "gallery" && (
          <button className="ghost" onClick={loadRuns}>
            ↻ refresh
          </button>
        )}
      </header>

      {error && <div className="banner error">backend error: {error}</div>}

      <main>
        {view === "gallery" &&
          (selectedRun == null ? (
            <RunGallery runs={runs} loading={loading} onOpen={(id) => setSelectedRun(id)} />
          ) : (
            <RunDetail id={selectedRun} onBack={() => setSelectedRun(null)} />
          ))}

        {view === "render" && (
          <div className="render-view">
            <div className="render-left">
              <JobBuilder onSubmitted={onJobSubmitted} />
              <h4 className="section">jobs</h4>
              <JobsPanel
                activeId={activeJob}
                onSelect={setActiveJob}
                onOpenRun={openRun}
                refreshKey={jobsRefresh}
              />
            </div>
            <div className="render-right">
              <LogConsole jobId={activeJob} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
