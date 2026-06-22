import { Fragment } from "react";

// "How it works" — a visual pipeline up top, then a real technical breakdown
// for devs: per-stage mechanics, on-disk data contracts, and the cross-cutting
// orchestration / web / data-contract layers.

const STEPS = [
  {
    n: 1,
    key: "sim",
    title: "Simulate",
    tech: "physics.py · pychrono",
    blurb: "Rigid-body N-body sim → motion data.",
    file: "physics.py · run_chrono_sim()",
    steps: [
      "Seed the RNG (SIM_SEED) and read BODY_COUNT.",
      "Spawn N rigid bodies (box / sphere / cylinder) at random points in a spawn sphere; random sizes; mass = density × volume.",
      "Assign random initial linear & angular velocity magnitudes (direction randomized).",
      "Each step: accumulate pairwise gravity F = g_eff·mᵢmⱼ / (r² + ε²), then DoStepDynamics(dt). g_eff = gravity_const / total_mass (body-count-independent); ε = Plummer softening (no blow-ups).",
      "Sample every body's pose at the frame cadence (steps_per_frame = ⌈hz / fps⌉).",
    ],
    inp: "resolved config — gravity_const, gravity_softening, spawn_*_vel_range, body count, duration, fps/hz, seed",
    out: "physics/motion_data.npz",
    schema:
      "npz[body_name] : float64 (F, 9)\n  [frame_idx, time, x, y, z, qw, qx, qy, qz]\n  coordinates are Chrono Y-up\n+ run_metadata.json : per-body {shape, dims, color}, fps, frames, seed",
  },
  {
    n: 2,
    key: "build",
    title: "Assemble",
    tech: "blender_driver.py · bpy + Alembic",
    blurb: "Bake motion into a renderable 3D scene.",
    file: "blender_driver.py → blender_stage.py",
    steps: [
      "Code-gen blender_stage.py — a Python script with the config values baked in (f-string template).",
      "Launch Blender headless: blender -b --python blender_stage.py.",
      "Rebuild each body as a primitive mesh; keyframe location + rotation_quaternion per frame, applying the Chrono Y-up → Blender Z-up transform (90° about X).",
      "Set scene fps = render fps (must match before export), then export the animated bodies as a time-based Alembic cache.",
      "Re-import the .abc (set_frame_range=False); build the camera (static or clump-tracking dolly w/ smoothing), AREA key/fill/rim lights, world backdrop, per-body materials.",
      "prep mode: save scene_edit.blend and stop (hand-edit in Blender); otherwise fall through to render.",
    ],
    inp: "motion_data.npz + run_metadata.json (+ optional .blend scene override)",
    out: "alembic/motion_data.abc  (+ scene_edit.blend in prep mode)",
    schema:
      "Alembic = time-sampled transform cache (one xform / body / frame).\nWhy Alembic: decouples sim from render — Blender just plays\nthe cache back, no per-frame Python.",
  },
  {
    n: 3,
    key: "render",
    title: "Render",
    tech: "Cycles (OptiX) · ffmpeg",
    blurb: "Path-trace frames on the GPU, stitch to video.",
    file: "blender_stage.py · enable_devices() → ffmpeg",
    steps: [
      "engine = CYCLES; pick GPU backend by priority OptiX → CUDA → HIP → oneAPI → Metal → CPU.",
      "Apply: samples (per preset), OptiX denoiser, use_persistent_data (reuse BVH across frames), adaptive sampling (threshold 0.01), max_bounces 4, resolution + fps from preset.",
      "render(animation=True): path-trace each frame on the GPU; a render_write handler writes rendered_frames/frame_####.png and streams progress.",
      "ffmpeg -framerate {fps} -i frame_%04d.png -c:v libx264 -pix_fmt yuv420p → rendered_frames.mp4.",
    ],
    inp: "alembic/motion_data.abc + the assembled scene",
    out: "rendered_frames/frame_####.png  →  rendered_frames.mp4",
    schema:
      "Per-frame PNG (RGB8) → H.264 MP4.\nPNGs kept on disk → renders are resumable & inspectable.",
  },
];

const ARTIFACTS = [
  { name: "motion_data.npz", sub: "(F, 9) · Y-up" },
  { name: "motion_data.abc", sub: "Alembic cache" },
];

const LAYERS = [
  {
    key: "orch",
    title: "Orchestration",
    tech: "run.py",
    points: [
      "argparse CLI: -q quality · -n bodies · -t seconds · -p prep · -r resume · -ph reuse-physics · -b scene · -c override · -s stitch · -f first-frame.",
      "config = deep_merge(DEFAULT_CONFIG, JSON from -c). Quality presets (low/high/final) set samples, resolution, fps, hz.",
      "Creates the next output/outputN/; writes config_used.json (resolved) + config_base.json (defaults snapshot).",
      "Drives physics → blender_driver → ffmpeg; unified logging to run.log / blender.log.",
    ],
  },
  {
    key: "web",
    title: "Web layer",
    tech: "FastAPI + React (web/)",
    points: [
      "jobmanager: one serial worker (a single GPU job at a time); spawns conda run --no-capture-output -n chrono python -u run.py … as its own process group so cancel kills the whole tree.",
      "Job records in memory, mirrored atomically to .jobs/jobs.json; the per-job log streams to the browser over SSE.",
      "Filesystem IS the database — runs.py reads output/; run_spec() diffs config_used vs config_base to reconstruct a job's overrides.",
      "React studio: builder (field registry → config_override), jobs panel w/ inline previews, gallery.",
    ],
  },
  {
    key: "contract",
    title: "The data contract",
    tech: "the narrow waist",
    points: [
      "Everything pinches down to: body definitions + per-frame poses (NPZ) → renderable scene (Alembic).",
      "The render half is simulation-agnostic — it only knows 'shapes with poses over time'.",
      "So a new sim type just has to emit this contract; the entire assemble + render half is reused unchanged.",
    ],
  },
];

function Card({ step }) {
  return (
    <div className={`arch-card s-${step.key}`}>
      <span className="arch-badge">{step.n}</span>
      <div className="arch-card-title">{step.title}</div>
      <div className="arch-card-tech">{step.tech}</div>
      <p className="arch-card-desc">{step.blurb}</p>
      <code className="arch-card-file">{step.file}</code>
    </div>
  );
}

function Wire({ artifact }) {
  return (
    <div className="arch-wire">
      <span className="arch-wire-chip">{artifact.name}</span>
      <span className="arch-wire-sub">{artifact.sub}</span>
      <span className="arch-wire-line" />
    </div>
  );
}

function DetailPanel({ step }) {
  return (
    <div className={`arch-dpanel s-${step.key}`}>
      <div className="arch-dhead">
        <span className="arch-dnum">{step.n}</span>
        <div>
          <div className="arch-dtitle">{step.title}</div>
          <code className="arch-dfile">{step.file}</code>
        </div>
      </div>
      <div className="arch-dbody">
        <div className="arch-dsteps">
          <div className="arch-dlabel">what happens</div>
          <ol>
            {step.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        </div>
        <div className="arch-dio">
          <div className="arch-dlabel">in</div>
          <p className="arch-din">{step.inp}</p>
          <div className="arch-dlabel">out</div>
          <code className="arch-dout">{step.out}</code>
          <pre className="arch-dschema">{step.schema}</pre>
        </div>
      </div>
    </div>
  );
}

export default function ArchDiagram() {
  return (
    <div className="arch-page">
      <div className="arch-head">
        <h2>How it works</h2>
        <p>
          A three-stage pipeline with the <span className="hl">filesystem as the bus</span>:
          each stage writes an artifact to <code>output/outputN/</code> and the next
          stage reads it. One run = one numbered directory holding its own config,
          intermediates, frames, and logs.
        </p>
      </div>

      {/* visual pipeline */}
      <div className="arch-stage">
        <div className="arch-end in">
          <div className="arch-end-title">Config / Presets</div>
          <div className="arch-end-sub">config.py · deep-merged override</div>
        </div>
        <div className="arch-drop" />
        <div className="arch-row">
          {STEPS.map((s, i) => (
            <Fragment key={s.key}>
              {i > 0 && <Wire artifact={ARTIFACTS[i - 1]} />}
              <Card step={s} />
            </Fragment>
          ))}
        </div>
        <div className="arch-drop">
          <span className="arch-drop-chip">frames → .mp4</span>
        </div>
        <div className="arch-end out">
          <div className="arch-end-title">Final Video</div>
          <div className="arch-end-sub">rendered_frames.mp4 + PNG frames</div>
        </div>
      </div>

      {/* technical breakdown */}
      <div className="arch-tech">
        <div className="arch-tech-head">Stage internals</div>
        {STEPS.map((s) => (
          <DetailPanel key={s.key} step={s} />
        ))}
      </div>

      {/* cross-cutting layers */}
      <div className="arch-tech">
        <div className="arch-tech-head">Cross-cutting</div>
        <div className="arch-layers">
          {LAYERS.map((l) => (
            <div key={l.key} className={`arch-layer l-${l.key}`}>
              <div className="arch-ltitle">{l.title}</div>
              <code className="arch-ltech">{l.tech}</code>
              <ul>
                {l.points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
