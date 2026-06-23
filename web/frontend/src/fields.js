// Schema-driven config <-> builder-form helpers. The field schema itself now
// lives in Python (config.py · FIELD_SCHEMA) and is fetched from
// /api/config/fields, so there's no hardcoded key list here to drift. These
// helpers just map an override blob to/from form state, given that schema.
//
// Form state is keyed by config key; a "range" field uses key__min / key__max.

// Physics uses |velocity| as a min→max band, so [-15,15] means "magnitude 15..15".
export function magBounds(arr) {
  if (!arr || arr.length < 2) return null;
  return arr.map((v) => Math.abs(v)).sort((a, b) => a - b);
}

export function rangesEqual(a, b) {
  return a && b && Number(a[0]) === Number(b[0]) && Number(a[1]) === Number(b[1]);
}

export const rangeMin = (key) => `${key}__min`;
export const rangeMax = (key) => `${key}__max`;

// Blank value for every field in the schema.
export function blankFields(schema) {
  const f = {};
  for (const fld of schema || []) {
    if (fld.type === "range") {
      f[rangeMin(fld.key)] = "";
      f[rangeMax(fld.key)] = "";
    } else if (fld.type === "bool") {
      f[fld.key] = false;
    } else {
      f[fld.key] = "";
    }
  }
  return f;
}

// Pull recognized keys out of a config_override into form values; unrecognized
// keys (e.g. camera_move, camera_look_at) pass through untouched in `rest`.
export function splitOverride(override, schema) {
  const o = { ...(override || {}) };
  const fields = {};
  for (const fld of schema || []) {
    if (fld.type === "range") {
      const b = magBounds(o[fld.key]);
      if (b) {
        fields[rangeMin(fld.key)] = b[0];
        fields[rangeMax(fld.key)] = b[1];
      }
    } else if (o[fld.key] != null) {
      fields[fld.key] = fld.type === "bool" ? !!o[fld.key] : o[fld.key];
    }
    delete o[fld.key];
  }
  return { fields, rest: Object.keys(o).length ? o : null };
}

// Form values to fill from resolved defaults — only those still blank.
export function fieldsFromDefaults(form, defaults, schema) {
  const out = {};
  for (const fld of schema || []) {
    if (fld.type === "range") {
      const b = magBounds(defaults?.[fld.key]);
      if (!b) continue;
      if (form[rangeMin(fld.key)] === "") out[rangeMin(fld.key)] = b[0];
      if (form[rangeMax(fld.key)] === "") out[rangeMax(fld.key)] = b[1];
    } else if (fld.type === "bool") {
      if (defaults?.[fld.key] != null) out[fld.key] = !!defaults[fld.key];
    } else if (form[fld.key] === "" && defaults?.[fld.key] != null) {
      out[fld.key] = defaults[fld.key];
    }
  }
  return out;
}

// Fold form fields back into a config_override, each only when it differs from
// the default (so untouched values stay implicit).
export function mergeOverride(form, base, defaults, schema) {
  const out = { ...(base || {}) };
  for (const fld of schema || []) {
    if (fld.type === "range") {
      if (form[rangeMin(fld.key)] === "" || form[rangeMax(fld.key)] === "") continue;
      const a = Number(form[rangeMin(fld.key)]);
      const b = Number(form[rangeMax(fld.key)]);
      if (Number.isNaN(a) || Number.isNaN(b)) continue;
      const range = [a, b].sort((x, y) => x - y);
      if (!rangesEqual(range, magBounds(defaults?.[fld.key]))) out[fld.key] = range;
    } else if (fld.type === "bool") {
      const want = !!form[fld.key];
      if (want !== !!defaults?.[fld.key]) out[fld.key] = want;
    } else {
      if (form[fld.key] === "") continue;
      const num = Number(form[fld.key]);
      if (!Number.isNaN(num) && num !== Number(defaults?.[fld.key])) out[fld.key] = num;
    }
  }
  return Object.keys(out).length ? out : null;
}

// Friendly label for an override key (jobs list "tweaked:" line). Uses the
// schema's label when available, else humanizes the key.
export function humanizeKey(key) {
  return String(key).replace(/_/g, " ");
}
export function overrideLabel(key, labelMap) {
  return (labelMap && labelMap[key]) || humanizeKey(key);
}
