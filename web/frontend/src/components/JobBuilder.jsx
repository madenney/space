import { useEffect, useRef, useState } from "react";
import { fetchDefaults, createJob, fetchPresets, savePreset } from "../api.js";

const BLANK = {
  name: "",
  quality: "low",
  num_bodies: 5,
  seconds: 1,
  gravity: "",  // gravity_const; filled from defaults on load
  linMin: "",   // spawn_lin_vel_range magnitude bounds
  linMax: "",
  angMin: "",   // spawn_ang_vel_range magnitude bounds
  angMax: "",
  first_frame: false,
  config_override: null,
};

// Physics uses |velocity| as a min→max band, so a [-15,15] range really means
// "speed magnitude 15..15". Reduce a raw range to sorted magnitude bounds.
function magBounds(arr) {
  if (!arr || arr.length < 2) return null;
  return arr.map((v) => Math.abs(v)).sort((a, b) => a - b);
}

function rangesEqual(a, b) {
  return a && b && Number(a[0]) === Number(b[0]) && Number(a[1]) === Number(b[1]);
}

// Pull the fields we give dedicated inputs (gravity, the two speed ranges) out
// of a config_override blob, leaving any other overrides in `rest`.
function splitOverride(override) {
  const o = { ...(override || {}) };
  const gravity = o.gravity_const ?? null;
  const lin = magBounds(o.spawn_lin_vel_range);
  const ang = magBounds(o.spawn_ang_vel_range);
  delete o.gravity_const;
  delete o.spawn_lin_vel_range;
  delete o.spawn_ang_vel_range;
  return { gravity, lin, ang, rest: Object.keys(o).length ? o : null };
}

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
        const lin = magBounds(d.spawn_lin_vel_range);
        const ang = magBounds(d.spawn_ang_vel_range);
        setForm((f) => ({
          ...f,
          quality: d.default_quality ?? f.quality,
          num_bodies: d.default_body_count ?? f.num_bodies,
          seconds: d.duration_seconds ?? f.seconds,
          gravity: f.gravity === "" && d.gravity_const != null ? d.gravity_const : f.gravity,
          linMin: f.linMin === "" && lin ? lin[0] : f.linMin,
          linMax: f.linMax === "" && lin ? lin[1] : f.linMax,
          angMin: f.angMin === "" && ang ? ang[0] : f.angMin,
          angMax: f.angMax === "" && ang ? ang[1] : f.angMax,
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
    const { gravity, lin, ang, rest } = splitOverride(seed.config_override);
    setForm((f) => ({
      ...f,
      quality: seed.quality ?? f.quality,
      num_bodies: seed.num_bodies ?? f.num_bodies,
      seconds: seed.seconds ?? f.seconds,
      gravity: gravity != null ? gravity : f.gravity,
      linMin: lin ? lin[0] : f.linMin,
      linMax: lin ? lin[1] : f.linMax,
      angMin: ang ? ang[0] : f.angMin,
      angMax: ang ? ang[1] : f.angMax,
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
    const { gravity, lin, ang, rest } = splitOverride(p.config_override);
    setForm((f) => ({
      ...f,
      quality: p.quality ?? f.quality,
      num_bodies: p.num_bodies ?? f.num_bodies,
      seconds: p.seconds ?? f.seconds,
      gravity: gravity != null ? gravity : f.gravity,
      linMin: lin ? lin[0] : f.linMin,
      linMax: lin ? lin[1] : f.linMax,
      angMin: ang ? ang[0] : f.angMin,
      angMax: ang ? ang[1] : f.angMax,
      first_frame: p.first_frame ?? f.first_frame,
      config_override: rest,
    }));
  }

  // Fold the gravity field back into config_override. Only include it when it
  // differs from the engine default, so an untouched value stays implicit.
  function mergedOverride() {
    const base = { ...(form.config_override || {}) };

    const g = Number(form.gravity);
    const dflt = defaults?.gravity_const;
    if (form.gravity !== "" && !Number.isNaN(g) && g !== dflt) {
      base.gravity_const = g;
    }

    // Speed ranges: send only when the user moved them off the defaults.
    const addRange = (key, minV, maxV, defArr) => {
      if (minV === "" || maxV === "") return;
      const lo = Number(minV);
      const hi = Number(maxV);
      if (Number.isNaN(lo) || Number.isNaN(hi)) return;
      const range = [lo, hi].sort((a, b) => a - b);
      if (!rangesEqual(range, magBounds(defArr))) base[key] = range;
    };
    addRange("spawn_lin_vel_range", form.linMin, form.linMax, defaults?.spawn_lin_vel_range);
    addRange("spawn_ang_vel_range", form.angMin, form.angMax, defaults?.spawn_ang_vel_range);

    return Object.keys(base).length ? base : null;
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
      <h3>New render</h3>

      <label>
        preset
        <select value={selectedPreset} onChange={(e) => applyPreset(e.target.value)}>
          <option value="">— none —</option>
          {presets.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

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
        gravity
        <span className="hint">pairwise attraction between bodies (default {defaults?.gravity_const ?? "0.0005"})</span>
        <input
          type="number"
          step={0.0001}
          min={0}
          value={form.gravity}
          onChange={(e) => set("gravity", e.target.value)}
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

      <label>
        duration (seconds)
        <input type="number" min={0.1} step={0.1} value={form.seconds} onChange={(e) => set("seconds", e.target.value)} />
      </label>

      <label className="checkbox">
        <input type="checkbox" checked={form.first_frame} onChange={(e) => set("first_frame", e.target.checked)} />
        first frame only (quick lighting check)
      </label>

      {overrideKeys.length > 0 && (
        <div className="override-info">
          + config override: {overrideKeys.map((k) => `${k}=${form.config_override[k]}`).join(", ")}
        </div>
      )}

      {error && <div className="banner error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

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

      <div className="save-preset">
        <input
          value={presetName}
          placeholder="save current as preset…"
          onChange={(e) => setPresetName(e.target.value)}
        />
        <button type="button" className="ghost" onClick={doSavePreset} disabled={!presetName.trim()}>
          save
        </button>
      </div>
    </form>
  );
}
