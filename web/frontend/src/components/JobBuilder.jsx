import { useEffect, useState } from "react";
import { fetchDefaults, createJob } from "../api.js";

export default function JobBuilder({ onSubmitted }) {
  const [defaults, setDefaults] = useState(null);
  const [form, setForm] = useState({
    name: "",
    quality: "low",
    num_bodies: 5,
    seconds: 1,
    first_frame: false,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchDefaults()
      .then((d) => {
        setDefaults(d);
        setForm((f) => ({
          ...f,
          quality: d.default_quality ?? f.quality,
          num_bodies: d.default_body_count ?? f.num_bodies,
          seconds: d.duration_seconds ?? f.seconds,
        }));
      })
      .catch((e) => setError(e.message));
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await createJob({
        name: form.name || null,
        quality: form.quality,
        num_bodies: Number(form.num_bodies),
        seconds: Number(form.seconds),
        first_frame: form.first_frame,
      });
      onSubmitted?.(job);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const qualities = defaults ? Object.keys(defaults.quality_presets || {}) : ["low"];
  const preset = defaults?.quality_presets?.[form.quality];

  return (
    <form className="builder" onSubmit={submit}>
      <h3>New render</h3>

      <label>
        name <span className="hint">optional</span>
        <input
          value={form.name}
          placeholder="auto"
          onChange={(e) => set("name", e.target.value)}
        />
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
        <input
          type="number"
          min={1}
          value={form.num_bodies}
          onChange={(e) => set("num_bodies", e.target.value)}
        />
      </label>

      <label>
        duration (seconds)
        <input
          type="number"
          min={0.1}
          step={0.1}
          value={form.seconds}
          onChange={(e) => set("seconds", e.target.value)}
        />
      </label>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.first_frame}
          onChange={(e) => set("first_frame", e.target.checked)}
        />
        first frame only (quick lighting check)
      </label>

      {error && <div className="banner error">{error}</div>}

      <button className="primary" type="submit" disabled={submitting}>
        {submitting ? "queuing…" : "▶ render"}
      </button>
    </form>
  );
}
