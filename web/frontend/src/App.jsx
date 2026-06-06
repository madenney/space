import { useEffect, useState } from "react";
import { fetchRuns, fetchRunSpec } from "./api.js";
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

  // Settings cloned into the builder from a previous job/run.
  const [builderSeed, setBuilderSeed] = useState(null);
  const [seedNonce, setSeedNonce] = useState(0);

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

  // Clone settings into the builder, then jump to the render view.
  function useSettings(spec) {
    setBuilderSeed(spec);
    setSeedNonce((n) => n + 1);
    setSelectedRun(null);
    setView("render");
  }

  async function useRunSettings(id) {
    try {
      useSettings(await fetchRunSpec(id));
    } catch (e) {
      setError(e.message);
    }
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
            <RunGallery
              runs={runs}
              loading={loading}
              onOpen={(id) => setSelectedRun(id)}
              onUseSettings={useRunSettings}
            />
          ) : (
            <RunDetail
              id={selectedRun}
              onBack={() => setSelectedRun(null)}
              onUseSettings={() => useRunSettings(selectedRun)}
            />
          ))}

        {view === "render" && (
          <div className="render-view">
            <div className="render-left">
              <JobBuilder
                onSubmitted={onJobSubmitted}
                seed={builderSeed}
                seedNonce={seedNonce}
              />
              <h4 className="section">jobs</h4>
              <JobsPanel
                activeId={activeJob}
                onSelect={setActiveJob}
                onOpenRun={openRun}
                onRerun={onJobSubmitted}
                onClone={useSettings}
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
