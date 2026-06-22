import { useEffect, useRef, useState } from "react";
import { fetchDefaults, createJob, fetchPresets, savePreset } from "../api.js";
import {
  blankFields,
  fieldsFromDefaults,
  splitOverride,
  mergeOverride,
} from "../fields.js";

const BLANK = {
  name: "",
  quality: "low",
  num_bodies: 5,
  seconds: 1,
  ...blankFields(), // gravity / move+spin speed / camera fields (see fields.js)
  first_frame: false,
  config_override: null,
};

// Handy starting angles (azimuth°, elevation°). Distance is left as-is.
const CAM_PRESETS = {
  "front": { az: 0, elev: 5 },
  "3/4 view": { az: 35, elev: 15 },
  "side": { az: 90, elev: 8 },
  "top-down": { az: 0, elev: 80 },
  "low / dramatic": { az: 20, elev: -10 },
};

export default function JobBuilder({ onSubmitted, seed, seedNonce }) {
  const [defaults, setDefaults] = useState(null);
  const seededRef = useRef(false);
  const [presets, setPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState("");
  const [form, setForm] = useState(BLANK);
  const [presetName, setPresetName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const loadPresets = () => fetchPresets().then(setPresets).catch(() => {});

  useEffect(() => {
    fetchDefaults()
      .then((d) => {
        setDefaults(d);
        // Don't clobber a cloned-in seed with the plain defaults.
        if (seededRef.current) return;
        setForm((f) => ({
          ...f,
          quality: d.default_quality ?? f.quality,
          num_bodies: d.default_body_count ?? f.num_bodies,
          seconds: d.duration_seconds ?? f.seconds,
          ...fieldsFromDefaults(f, d),
        }));
      })
      .catch((e) => setError(e.message));
    loadPresets();
  }, []);

  // Populate the form from a previous job/run ("use as template").
  useEffect(() => {
    if (!seed) return;
    seededRef.current = true;
    setSelectedPreset("");
    const { fields, rest } = splitOverride(seed.config_override);
    setForm((f) => ({
      ...f,
      quality: seed.quality ?? f.quality,
      num_bodies: seed.num_bodies ?? f.num_bodies,
      seconds: seed.seconds ?? f.seconds,
      ...fields,
      first_frame: seed.first_frame ?? false,
      config_override: rest,
      name: seed.name ? `${seed.name}-copy` : f.name,
    }));
    setNotice("loaded settings from a previous render — tweak and hit render");
  }, [seedNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  function applyPreset(name) {
    setSelectedPreset(name);
    setNotice(null);
    if (!name) {
      set("config_override", null);
      return;
    }
    const p = presets.find((x) => x.name === name);
    if (!p) return;
    const { fields, rest } = splitOverride(p.config_override);
    setForm((f) => ({
      ...f,
      quality: p.quality ?? f.quality,
      num_bodies: p.num_bodies ?? f.num_bodies,
      seconds: p.seconds ?? f.seconds,
      ...fields,
      first_frame: p.first_frame ?? f.first_frame,
      config_override: rest,
    }));
  }

  // Set azimuth/elevation from a named angle preset (leaves distance alone).
  function applyCamPreset(name) {
    const p = CAM_PRESETS[name];
    if (!p) return;
    setForm((f) => ({ ...f, camAz: p.az, camElev: p.elev }));
  }

  // Fold the gravity field back into config_override. Only include it when it
  // differs from its default, so an untouched value stays implicit. The field
  // <-> config-key mapping lives in fields.js (single source of truth).
  function mergedOverride() {
    return mergeOverride(form, form.config_override, defaults);
  }

  function payload(extra) {
    return {
      name: form.name || null,
      quality: form.quality,
      num_bodies: Number(form.num_bodies),
      seconds: Number(form.seconds),
      first_frame: form.first_frame,
      config_override: mergedOverride(),
      ...extra,
    };
  }

  async function run(extra, e) {
    if (e) e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await createJob(payload(extra));
      onSubmitted?.(job);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function doSavePreset() {
    if (!presetName.trim()) return;
    setError(null);
    try {
      await savePreset({
        name: presetName.trim(),
        quality: form.quality,
        num_bodies: Number(form.num_bodies),
        seconds: Number(form.seconds),
        first_frame: form.first_frame,
        config_override: mergedOverride(),
      });
      await loadPresets();
      setSelectedPreset(presetName.trim());
      setNotice(`saved preset “${presetName.trim()}”`);
      setPresetName("");
    } catch (err) {
      setError(err.message);
    }
  }

  const qualities = defaults ? Object.keys(defaults.quality_presets || {}) : ["low"];
  const preset = defaults?.quality_presets?.[form.quality];
  const overrideKeys = form.config_override ? Object.keys(form.config_override) : [];

  return (
    <form className="builder" onSubmit={(e) => run({}, e)}>
      <div className="builder-head">
        <h3>New render</h3>
        <select
          className="preset-pick"
          value={selectedPreset}
          onChange={(e) => applyPreset(e.target.value)}
          title="Load a saved preset"
        >
          <option value="">preset…</option>
          {presets.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {/* Basics — the essentials, open by default */}
      <details className="group" open>
        <summary>Basics</summary>
        <div className="group-body">
          <label>
            name <span className="hint">optional</span>
            <input value={form.name} placeholder="auto" onChange={(e) => set("name", e.target.value)} />
          </label>
          <label>
            quality
            <select value={form.quality} onChange={(e) => set("quality", e.target.value)}>
              {qualities.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </label>
          {preset && (
            <div className="preset-info">
              {preset.res_x}×{preset.res_y} · {preset.samples} samples · {preset.fps} fps
            </div>
          )}
          <label>
            bodies
            <input type="number" min={1} value={form.num_bodies} onChange={(e) => set("num_bodies", e.target.value)} />
          </label>
          <label>
            duration (seconds)
            <input type="number" min={0.1} step={0.1} value={form.seconds} onChange={(e) => set("seconds", e.target.value)} />
          </label>
        </div>
      </details>

      {/* Physics — collapsed; tweak the simulation flavor */}
      <details className="group">
        <summary>Physics</summary>
        <div className="group-body">
          <label>
            gravity
            <span className="hint">attraction strength, independent of body count (~500 lively; ≳2000 unstable)</span>
            <input
              type="number"
              step={0.0001}
              min={0}
              value={form.gravity}
              onChange={(e) => set("gravity", e.target.value)}
            />
          </label>
          <label>
            gravity softening
            <span className="hint">cushions close encounters so the sim can't blow up (bigger = gentler)</span>
            <input
              type="number"
              step={0.1}
              min={0}
              value={form.gravSoft}
              onChange={(e) => set("gravSoft", e.target.value)}
            />
          </label>
          <label>
            move speed <span className="hint">initial linear speed range (min → max)</span>
            <div className="range-row">
              <input
                type="number" min={0} step={0.5}
                value={form.linMin} onChange={(e) => set("linMin", e.target.value)}
              />
              <span className="range-sep">→</span>
              <input
                type="number" min={0} step={0.5}
                value={form.linMax} onChange={(e) => set("linMax", e.target.value)}
              />
            </div>
          </label>
          <label>
            spin speed <span className="hint">initial angular speed range (min → max)</span>
            <div className="range-row">
              <input
                type="number" min={0} step={0.5}
                value={form.angMin} onChange={(e) => set("angMin", e.target.value)}
              />
              <span className="range-sep">→</span>
              <input
                type="number" min={0} step={0.5}
                value={form.angMax} onChange={(e) => set("angMax", e.target.value)}
              />
            </div>
          </label>
        </div>
      </details>

      {/* Camera — collapsed; applies to new prep / render jobs */}
      <details className="group">
        <summary>Camera</summary>
        <div className="group-body">
          <label>
            angle preset
            <select value="" onChange={(e) => applyCamPreset(e.target.value)}>
              <option value="">— pick an angle —</option>
              {Object.keys(CAM_PRESETS).map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            distance <span className="hint">how far back (default {defaults?.camera_radius ?? 40})</span>
            <input type="number" min={1} step={1} value={form.camDist} onChange={(e) => set("camDist", e.target.value)} />
          </label>
          <label>
            azimuth° <span className="hint">spin around the scene (0 = front)</span>
            <input type="number" step={5} value={form.camAz} onChange={(e) => set("camAz", e.target.value)} />
          </label>
          <label>
            elevation° <span className="hint">height angle (− below, + above)</span>
            <input type="number" step={5} value={form.camElev} onChange={(e) => set("camElev", e.target.value)} />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={!!form.trackCog}
              onChange={(e) => set("trackCog", e.target.checked)}
            />
            keep swarm centered <span className="hint">locks onto the densest clump (fixed distance)</span>
          </label>
          <label>
            cam smoothing (s) <span className="hint">de-shakes the tracking; 0 = raw, higher = smoother</span>
            <input
              type="number" min={0} step={0.1}
              value={form.camSmooth}
              onChange={(e) => set("camSmooth", e.target.value)}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={!!form.originMarker}
              onChange={(e) => set("originMarker", e.target.checked)}
            />
            origin marker <span className="hint">static red cube at 0,0,0</span>
          </label>
        </div>
      </details>

      {overrideKeys.length > 0 && (
        <div className="override-info">
          + override: {overrideKeys.map((k) => `${k}=${form.config_override[k]}`).join(", ")}
        </div>
      )}

      {error && <div className="banner error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      {/* Actions — always visible */}
      <div className="builder-actions">
        <label className="checkbox">
          <input type="checkbox" checked={form.first_frame} onChange={(e) => set("first_frame", e.target.checked)} />
          first frame only (quick lighting check)
        </label>
        <button className="primary" type="submit" disabled={submitting}>
          {submitting ? "queuing…" : "▶ render"}
        </button>
        <button
          className="secondary"
          type="button"
          disabled={submitting}
          onClick={() => run({ prep_scene: true })}
          title="Run the sim and build an editable .blend, then stop before rendering"
        >
          🎬 prep &amp; edit in Blender
        </button>
        <div className="prep-help">
          builds an editable scene → open it in Blender to set camera/lights → then “render this scene” from the job below.
        </div>
      </div>

      {/* Save preset — collapsed; out of the way until needed */}
      <details className="group">
        <summary>Save as preset</summary>
        <div className="group-body">
          <div className="save-preset">
            <input
              value={presetName}
              placeholder="name this preset…"
              onChange={(e) => setPresetName(e.target.value)}
            />
            <button type="button" className="ghost" onClick={doSavePreset} disabled={!presetName.trim()}>
              save
            </button>
          </div>
        </div>
      </details>
    </form>
  );
}
