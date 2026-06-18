// Single source of truth for the physics/camera tunables the builder exposes.
// Each entry maps config_override key(s) <-> builder form field(s). To add a
// knob: add one entry here (+ its JSX input in JobBuilder) — the split / merge /
// defaults / preset / jobs-label logic all derive from this list.

// Physics uses |velocity| as a min→max band, so [-15,15] really means
// "speed magnitude 15..15". Reduce a raw range to sorted magnitude bounds.
export function magBounds(arr) {
  if (!arr || arr.length < 2) return null;
  return arr.map((v) => Math.abs(v)).sort((a, b) => a - b);
}

export function rangesEqual(a, b) {
  return a && b && Number(a[0]) === Number(b[0]) && Number(a[1]) === Number(b[1]);
}

// Scalar tunables: one config key <-> one form field.
export const NUMBER_FIELDS = [
  { configKey: "gravity_const", form: "gravity", label: "gravity" },
  { configKey: "camera_radius", form: "camDist", label: "cam distance" },
  { configKey: "camera_azimuth", form: "camAz", label: "cam angle" },
  { configKey: "camera_elevation", form: "camElev", label: "cam height" },
];

// Range tunables: one config key (a [min,max] magnitude band) <-> two form fields.
export const RANGE_FIELDS = [
  { configKey: "spawn_lin_vel_range", min: "linMin", max: "linMax", label: "move speed" },
  { configKey: "spawn_ang_vel_range", min: "angMin", max: "angMax", label: "spin speed" },
];

// Blank ("") values for every registered form field.
export function blankFields() {
  const f = {};
  for (const n of NUMBER_FIELDS) f[n.form] = "";
  for (const r of RANGE_FIELDS) {
    f[r.min] = "";
    f[r.max] = "";
  }
  return f;
}

// Pull registered keys out of a config_override blob into form values, leaving
// anything unrecognized in `rest` (passed through untouched).
export function splitOverride(override) {
  const o = { ...(override || {}) };
  const fields = {};
  for (const n of NUMBER_FIELDS) {
    if (o[n.configKey] != null) fields[n.form] = o[n.configKey];
    delete o[n.configKey];
  }
  for (const r of RANGE_FIELDS) {
    const b = magBounds(o[r.configKey]);
    if (b) {
      fields[r.min] = b[0];
      fields[r.max] = b[1];
    }
    delete o[r.configKey];
  }
  return { fields, rest: Object.keys(o).length ? o : null };
}

// Form-field values to fill from the resolved defaults — only those still "".
export function fieldsFromDefaults(form, defaults) {
  const out = {};
  for (const n of NUMBER_FIELDS) {
    if (form[n.form] === "" && defaults?.[n.configKey] != null) {
      out[n.form] = defaults[n.configKey];
    }
  }
  for (const r of RANGE_FIELDS) {
    const b = magBounds(defaults?.[r.configKey]);
    if (!b) continue;
    if (form[r.min] === "") out[r.min] = b[0];
    if (form[r.max] === "") out[r.max] = b[1];
  }
  return out;
}

// Fold the registered form fields back into a config_override, including each
// only when it differs from the default (so untouched values stay implicit).
export function mergeOverride(form, base, defaults) {
  const out = { ...(base || {}) };
  for (const n of NUMBER_FIELDS) {
    if (form[n.form] === "") continue;
    const num = Number(form[n.form]);
    if (!Number.isNaN(num) && num !== Number(defaults?.[n.configKey])) {
      out[n.configKey] = num;
    }
  }
  for (const r of RANGE_FIELDS) {
    if (form[r.min] === "" || form[r.max] === "") continue;
    const a = Number(form[r.min]);
    const b = Number(form[r.max]);
    if (Number.isNaN(a) || Number.isNaN(b)) continue;
    const range = [a, b].sort((x, y) => x - y);
    if (!rangesEqual(range, magBounds(defaults?.[r.configKey]))) out[r.configKey] = range;
  }
  return Object.keys(out).length ? out : null;
}

// Friendly label for an override key (for the jobs list "tweaked:" line).
const LABELS = Object.fromEntries(
  [...NUMBER_FIELDS, ...RANGE_FIELDS].map((f) => [f.configKey, f.label])
);
export function overrideLabel(key) {
  return LABELS[key] || key;
}
