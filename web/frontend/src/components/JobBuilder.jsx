import { useEffect, useRef, useState } from "react";
import { fetchDefaults, fetchFields, createJob, fetchPresets, savePreset } from "../api.js";
import {
  blankFields,
  fieldsFromDefaults,
  splitOverride,
  mergeOverride,
  rangeMin,
  rangeMax,
} from "../fields.js";

// Top-level request fields (not config_override) + camera-move widget state.
const BASE = {
  name: "",
  quality: "low",
  num_bodies: 5,
  seconds: 1,
  first_frame: false,
  config_override: null,
  // camera-move widget (nested camera_move / camera_look_at spec)
  _moveMode: "",
  _lookAt: "",
  _orbitDeg: "",
  _radiusTo: "",
  _elevTo: "",
  _scenario: "", // "" = config default (rigid)
};

// Handy starting angles (azimuth°, elevation°). Distance is left as-is.
const CAM_PRESETS = {
  "front": { az: 0, elev: 5 },
  "3/4 view": { az: 35, elev: 15 },
  "side": { az: 90, elev: 8 },
  "top-down": { az: 0, elev: 80 },
  "low / dramatic": { az: 20, elev: -10 },
};

// Pull non-schema override keys (camera_move/look_at spec + scenario) out of a
// `rest` blob into the widget's flat state (so loading a preset/run shows them).
function splitExtras(rest) {
  const r = { ...(rest || {}) };
  const st = { _moveMode: "", _lookAt: "", _orbitDeg: "", _radiusTo: "", _elevTo: "", _scenario: "" };
  const mv = r.camera_move;
  if (mv && mv.mode) {
    st._moveMode = mv.mode;
    if (mv.orbit_degrees != null) st._orbitDeg = mv.orbit_degrees;
    if (mv.radius_to != null) st._radiusTo = mv.radius_to;
    if (mv.elevation_to != null) st._elevTo = mv.elevation_to;
  }
  if (r.camera_look_at) st._lookAt = r.camera_look_at;
  if (r.scenario) st._scenario = r.scenario;
  delete r.camera_move;
  delete r.camera_look_at;
  delete r.scenario;
  return { st, rest: Object.keys(r).length ? r : null };
}

// Widget state -> override extras (camera_move/look_at + scenario), only when set.
function buildExtras(form) {
  const out = {};
  if (form._moveMode) {
    const m = { mode: form._moveMode };
    if (form._moveMode === "orbit") {
      if (form._orbitDeg !== "") m.orbit_degrees = Number(form._orbitDeg);
      if (form._radiusTo !== "") m.radius_to = Number(form._radiusTo);
      if (form._elevTo !== "") m.elevation_to = Number(form._elevTo);
    }
    out.camera_move = m;
  }
  if (form._lookAt) out.camera_look_at = form._lookAt;
  if (form._scenario) out.scenario = form._scenario;
  return out;
}

export default function JobBuilder({ onSubmitted, seed, seedNonce }) {
  const [defaults, setDefaults] = useState(null);
  const [fields, setFields] = useState([]); // FIELD_SCHEMA from the backend
  const [camMove, setCamMove] = useState(null); // CAMERA_MOVE_SCHEMA
  const [scenarioChoices, setScenarioChoices] = useState([]);
  const seededRef = useRef(false);
  const appliedSeed = useRef(-1);
  const [presets, setPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState("");
  const [form, setForm] = useState(BASE);
  const [presetName, setPresetName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const loadPresets = () => fetchPresets().then(setPresets).catch(() => {});

  // Load defaults + the field schema together.
  useEffect(() => {
    Promise.all([fetchDefaults(), fetchFields()])
      .then(([d, s]) => {
        const schema = s.fields || [];
        setDefaults(d);
        setFields(schema);
        setCamMove(s.camera_move || null);
        setScenarioChoices(s.scenarios || []);
        setForm((f) => {
          const withKeys = { ...blankFields(schema), ...f };
          if (seededRef.current) return withKeys;
          return {
            ...withKeys,
            quality: d.default_quality ?? withKeys.quality,
            num_bodies: d.default_body_count ?? withKeys.num_bodies,
            seconds: d.duration_seconds ?? withKeys.seconds,
            ...fieldsFromDefaults(withKeys, d, schema),
          };
        });
      })
      .catch((e) => setError(e.message));
    loadPresets();
  }, []);

  // Populate from a previous job/run ("use as template"). Re-runs once the
  // schema is available if the seed arrived first.
  useEffect(() => {
    if (!seed || !fields.length) return;
    if (appliedSeed.current === seedNonce) return;
    appliedSeed.current = seedNonce;
    seededRef.current = true;
    setSelectedPreset("");
    const { fields: vals, rest } = splitOverride(seed.config_override, fields);
    const { st, rest: rest2 } = splitExtras(rest);
    setForm((f) => ({
      ...f,
      quality: seed.quality ?? f.quality,
      num_bodies: seed.num_bodies ?? f.num_bodies,
      seconds: seed.seconds ?? f.seconds,
      ...vals,
      ...st,
      first_frame: seed.first_frame ?? false,
      config_override: rest2,
      name: seed.name ? `${seed.name}-copy` : f.name,
    }));
    setNotice("loaded settings from a previous render — tweak and hit render");
  }, [seedNonce, fields]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const { fields: vals, rest } = splitOverride(p.config_override, fields);
    const { st, rest: rest2 } = splitExtras(rest);
    setForm((f) => ({
      ...f,
      quality: p.quality ?? f.quality,
      num_bodies: p.num_bodies ?? f.num_bodies,
      seconds: p.seconds ?? f.seconds,
      ...vals,
      ...st,
      first_frame: p.first_frame ?? f.first_frame,
      config_override: rest2,
    }));
  }

  function applyCamPreset(name) {
    const p = CAM_PRESETS[name];
    if (!p) return;
    setForm((f) => ({ ...f, camera_azimuth: p.az, camera_elevation: p.elev }));
  }

  function mergedOverride() {
    const base = mergeOverride(form, form.config_override, defaults, fields) || {};
    const merged = { ...base, ...buildExtras(form) };
    return Object.keys(merged).length ? merged : null;
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

  // Render one schema field (number / range / bool) by descriptor.
  function Field(fld) {
    if (fld.type === "bool") {
      return (
        <label className="checkbox" key={fld.key}>
          <input
            type="checkbox"
            checked={!!form[fld.key]}
            onChange={(e) => set(fld.key, e.target.checked)}
          />
          {fld.label} {fld.hint && <span className="hint">{fld.hint}</span>}
        </label>
      );
    }
    if (fld.type === "range") {
      return (
        <label key={fld.key}>
          {fld.label} {fld.hint && <span className="hint">{fld.hint}</span>}
          <div className="range-row">
            <input
              type="number" min={fld.min} step={fld.step}
              value={form[rangeMin(fld.key)] ?? ""}
              onChange={(e) => set(rangeMin(fld.key), e.target.value)}
            />
            <span className="range-sep">→</span>
            <input
              type="number" min={fld.min} step={fld.step}
              value={form[rangeMax(fld.key)] ?? ""}
              onChange={(e) => set(rangeMax(fld.key), e.target.value)}
            />
          </div>
        </label>
      );
    }
    return (
      <label key={fld.key}>
        {fld.label} {fld.hint && <span className="hint">{fld.hint}</span>}
        <input
          type="number" min={fld.min} step={fld.step}
          value={form[fld.key] ?? ""}
          onChange={(e) => set(fld.key, e.target.value)}
        />
      </label>
    );
  }

  const groupFields = (g) => fields.filter((f) => f.group === g);
  const qualities = defaults ? Object.keys(defaults.quality_presets || {}) : ["low"];
  const preset = defaults?.quality_presets?.[form.quality];
  const overrideKeys = form.config_override ? Object.keys(form.config_override) : [];
  const orbitParams = camMove?.modes?.find((m) => m.mode === "orbit")?.params || [];

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
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      </div>

      {/* Basics */}
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
                <option key={q} value={q}>{q}</option>
              ))}
            </select>
          </label>
          {preset && (
            <div className="preset-info">
              {preset.res_x}×{preset.res_y} · {preset.samples} samples · {preset.fps} fps
            </div>
          )}
          {scenarioChoices.length > 0 && (
            <label>
              scenario
              <select value={form._scenario} onChange={(e) => set("_scenario", e.target.value)}>
                <option value="">default (rigid bodies)</option>
                {scenarioChoices.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </label>
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

      {/* Physics — rendered from the schema */}
      <details className="group">
        <summary>Physics</summary>
        <div className="group-body">{groupFields("physics").map(Field)}</div>
      </details>

      {/* Camera — schema fields + angle presets + the move widget */}
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
          {groupFields("camera").map(Field)}

          {/* Camera move (nested camera_move / camera_look_at spec) */}
          {camMove && (
            <div className="cam-move">
              <div className="cam-move-title">camera move</div>
              <label>
                motion
                <select value={form._moveMode} onChange={(e) => set("_moveMode", e.target.value)}>
                  <option value="">default (from “keep centered”)</option>
                  {camMove.modes.map((m) => (
                    <option key={m.mode} value={m.mode}>{m.label}</option>
                  ))}
                </select>
              </label>
              <label>
                {camMove.look_at.label}
                <select value={form._lookAt} onChange={(e) => set("_lookAt", e.target.value)}>
                  <option value="">default</option>
                  {camMove.look_at.options.map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              </label>
              {form._moveMode === "orbit" &&
                orbitParams.map((p) => (
                  <label key={p.key}>
                    {p.label} {p.optional && <span className="hint">optional</span>}
                    <input
                      type="number" step={p.step}
                      value={form[p.key === "orbit_degrees" ? "_orbitDeg" : p.key === "radius_to" ? "_radiusTo" : "_elevTo"] ?? ""}
                      placeholder={p.default != null ? String(p.default) : ""}
                      onChange={(e) =>
                        set(p.key === "orbit_degrees" ? "_orbitDeg" : p.key === "radius_to" ? "_radiusTo" : "_elevTo", e.target.value)
                      }
                    />
                  </label>
                ))}
              {form._moveMode === "keyframes" && (
                <div className="hint">authored waypoints: set camera_move.keyframes via a preset/override</div>
              )}
            </div>
          )}
        </div>
      </details>

      {overrideKeys.length > 0 && (
        <div className="override-info">
          + override: {overrideKeys.map((k) => `${k}=${JSON.stringify(form.config_override[k])}`).join(", ")}
        </div>
      )}

      {error && <div className="banner error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

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
