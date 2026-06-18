import { useEffect, useState } from "react";
import { fetchRuns, fetchRunSpec } from "./api.js";
import RunGallery from "./components/RunGallery.jsx";
import RunDetail from "./components/RunDetail.jsx";
import JobBuilder from "./components/JobBuilder.jsx";
import JobsPanel from "./components/JobsPanel.jsx";
import LogConsole from "./components/LogConsole.jsx";
import HotkeysModal from "./components/HotkeysModal.jsx";

// Persisted UI state — survives closing/reopening the tab or browser.
const UI_KEY = "studio.ui.v1";
function loadUI() {
  try {
    return JSON.parse(localStorage.getItem(UI_KEY)) || {};
  } catch {
    return {};
  }
}

export default function App() {
  // A URL hash (#render/<jobId>) wins if present; otherwise restore where we
  // left off last time, then fall back to the gallery.
  const initialHash = window.location.hash;
  const saved = loadUI();
  const [view, setView] = useState(() =>
    initialHash.startsWith("#render") ? "render" : saved.view ?? "gallery"
  ); // "gallery" | "render"
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(saved.selectedRun ?? null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [activeJob, setActiveJob] = useState(() => {
    const m = initialHash.match(/^#render\/(\d+)$/);
    if (m) return Number(m[1]);
    return saved.activeJob ?? null;
  });
  const [jobsRefresh, setJobsRefresh] = useState(0);
  const [showHotkeys, setShowHotkeys] = useState(false);

  // Remember the current spot whenever it changes.
  useEffect(() => {
    localStorage.setItem(
      UI_KEY,
      JSON.stringify({ view, selectedRun, activeJob })
    );
  }, [view, selectedRun, activeJob]);

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
        <button className="ghost" onClick={() => setShowHotkeys(true)}>
          ⌨ blender keys
        </button>
        {view === "gallery" && (
          <button className="ghost" onClick={loadRuns}>
            ↻ refresh
          </button>
        )}
      </header>

      {showHotkeys && <HotkeysModal onClose={() => setShowHotkeys(false)} />}

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
            <div className="render-builder">
              <JobBuilder
                onSubmitted={onJobSubmitted}
                seed={builderSeed}
                seedNonce={seedNonce}
              />
            </div>
            <div className="render-jobs">
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
            <aside className="render-log">
              <LogConsole jobId={activeJob} />
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
