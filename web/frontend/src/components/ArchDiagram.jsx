import { Fragment } from "react";
import InputWiring from "./InputWiring.jsx";

// "How it works" — a boring, accurate, text-heavy walkthrough of the real
// pipeline. Each stage says what runs, in what tool, which files matter, and
// what goes in vs. out. Kept in sync with the code by hand; if you change the
// pipeline, change this.

// Compact top ribbon: the data as it flows end to end.
const FLOW = [
  ["config.py", "defaults + overrides"],
  ["run.py", "orchestrator"],
  ["Project Chrono", "physics"],
  ["motion_data.npz", "the contract"],
  ["blender_stage.py", "in Blender"],
  ["Cycles", "render"],
  ["frame_####.png", "frames"],
  ["ffmpeg", "encode"],
  ["rendered_frames.mp4", "result"],
];

const STAGES = [
  {
    n: "0",
    title: "Orchestration",
    tool: "run.py  (host Python)",
    role: "The conductor. One process that resolves config, creates the run directory, and calls each stage in order. Everything below is launched from here.",
    what: [
      "Parse CLI args and pick a mode: new run · -r resume (re-render missing frames) · -ph reuse another run's physics · -s stitch only · -p prep an editable .blend.",
      "Resolve config: copy.deepcopy(DEFAULT_CONFIG) then deep-merge any JSON override on top (config.py · load_config).",
      "Create output/outputN/ with physics/, alembic/, rendered_frames/ subdirs.",
      "Snapshot config_used.json (what this run used) and config_base.json (the defaults it was built against, so later 'what changed' diffs are stable).",
      "Tee all stdout/stderr to run.log, then run physics → render → encode.",
    ],
    files: ["run.py · main()", "config.py · DEFAULT_CONFIG, load_config(), _deep_merge()"],
    inp: "CLI flags (-q quality, -n bodies, -t seconds, …) + optional config override JSON",
    out: "output/outputN/ skeleton + config_used.json + config_base.json + run.log",
  },
  {
    n: "1",
    title: "Physics simulation",
    tool: "physics.py  →  Project Chrono (pychrono, conda env 'chrono')",
    role: "Where the motion is actually computed. Runs in the chrono conda environment because pychrono lives there. No graphics here at all — pure rigid-body dynamics.",
    what: [
      "Seed the RNG and read the body count. Spawn N rigid bodies (box / sphere / cylinder, weighted by shape_weights) at random points inside a spawn sphere; random sizes; mass = body_density × volume.",
      "Give each body a random initial linear and angular velocity (magnitude from spawn_*_vel_range, direction randomized).",
      "Per physics step: accumulate pairwise gravity  F = g_eff·mᵢmⱼ / (r² + ε²)  as an external force on every body, then advance with DoStepDynamics(dt).",
      "g_eff = gravity_const / total_mass makes strength body-count-independent; ε (gravity_softening) is Plummer softening so close encounters don't blow up to infinity.",
      "Sample every body's pose at the frame cadence: steps_per_frame = ⌈physics_hz / fps⌉. Higher hz = more accurate integration, same number of output frames.",
    ],
    files: ["physics.py · run_chrono_sim()"],
    inp: "resolved config: gravity_const, gravity_softening, spawn radius/speeds, body_density, shape_weights, body count, duration, fps, hz, seed",
    out: "physics/motion_data.npz  +  run_metadata.json",
    ui: "Builder: quality (hz/fps), bodies (-n), duration (-t), gravity, gravity softening, move speed, spin speed.",
  },
  {
    n: "2",
    title: "The data contract",
    tool: "motion_data.npz  +  run_metadata.json  (the narrow waist)",
    role: "The single interface between physics and rendering. Physics knows nothing about Blender; Blender knows nothing about Chrono. They only agree on this file format — which is why you can reuse physics (-ph) or resume a render (-r) without re-running the sim.",
    what: [
      "motion_data.npz: one float64 array per body, keyed by body name, shape (frames, 9).",
      "Each row is  [frame_idx, time, x, y, z, qw, qx, qy, qz]  in Chrono's Y-up coordinates.",
      "run_metadata.json: the per-body table {name, shape, dims, color}, plus fps, frame count, body_count, seed, obstacles. Everything the renderer needs that isn't poses.",
      "Because both are plain files on disk, a run directory is fully self-describing and any stage can be re-entered from it.",
    ],
    files: ["written by physics.py", "read by blender_stage.py"],
    inp: "—",
    out: "—",
    contract: true,
  },
  {
    n: "3",
    title: "Render-stage launch",
    tool: "blender_driver.py  →  spawns Blender headless",
    role: "The bridge from host Python into Blender. It does NOT generate code — it copies a static script in and launches Blender on it with a few flags.",
    what: [
      "prepare_blender_stage(): copy the static blender_stage.py into the run dir (provenance + standalone debugging), and build the argv that parameterizes it.",
      "find_blender(): locate the Blender binary (bundled blender-4.2.0-linux-x64/, or $BLENDER_BIN, or PATH).",
      "run_blender(): launch  blender -b --python-exit-code 1 -P blender_stage.py -- --run-dir <dir> --quality <q> [--resume-frame N] [--first-frame] [--stop-after-scene] …",
      "Stream Blender's stdout: write everything to blender.log, collapse the noisy per-tile spam, keep our own 'Rendering frames X/Y' progress on the console.",
      "--python-exit-code 1 makes a Python error inside Blender fail the whole job (Blender otherwise exits 0 even when the script raises).",
    ],
    files: ["blender_driver.py · prepare_blender_stage(), find_blender(), run_blender()"],
    inp: "run dir + quality + frame flags",
    out: "a running Blender process · blender.log",
    ui: "Jobs: ‘reuse physics’ (-ph) re-enters the pipeline HERE — skips stage 1, renders fresh from an existing run's NPZ.",
  },
  {
    n: "4",
    title: "Inside Blender — build & bake",
    tool: "blender_stage.py  (Blender's bundled Python, with bpy)",
    role: "Stage one of two inside Blender. Turns the NPZ poses into animated meshes and bakes them to an Alembic cache. This is a real, editable file now (not a generated string) — it reads the run's own config/metadata/NPZ at startup.",
    what: [
      "Parse argv after '--', then load config_used.json + run_metadata.json + the NPZ from --run-dir. Nothing is baked in.",
      "For each body: create the matching primitive (cube / uv_sphere / cylinder) sized from its dims.",
      "Keyframe its location and rotation_quaternion on every frame straight from the NPZ rows, converting Chrono Y-up → Blender Z-up via map_pos / map_quat (a 90° rotation about X).",
      "Set the scene fps to the render fps BEFORE export (Alembic is time-based; a mismatch silently drops frames), then export the animated meshes to alembic/motion_data.abc.",
      "Why Alembic: it's a compact baked point-cache. Baking the heavy per-body animation once lets Cycles stream it efficiently and cleanly separates 'what moved' from 'how it looks'.",
    ],
    files: ["blender_stage.py · stage 1 (load → keyframe → alembic_export)", "map_pos(), map_quat()"],
    inp: "motion_data.npz + run_metadata.json + config_used.json",
    out: "alembic/motion_data.abc",
    ui: "Gallery: ‘resume render’ (-r) re-enters around here — skips stage 1, keeps frames already on disk, renders the rest.",
  },
  {
    n: "5",
    title: "Inside Blender — assemble & render",
    tool: "blender_stage.py  →  Cycles (GPU)",
    role: "Stage two: rebuild a clean scene, place camera and lights, configure Cycles, and render frames. The camera move happens here (see the Camera panel below).",
    what: [
      "Clear the scene (or open a .blend override), re-import the Alembic with set_frame_range=False, then re-assert the frame range from the physics frame count.",
      "Materials: give each body a Principled BSDF tinted to its color (unless a prepared scene says to preserve materials). Add obstacles from metadata if any.",
      "World: a flat gray 'void' background from world_color, or an HDRI if configured.",
      "Camera: resolve_camera() → a move spec, interpreted into per-frame keyframes. Lights: AREA key / fill / rim, auto-aimed at the origin. Optional red origin marker.",
      "Cycles settings from the quality preset: samples, resolution, OptiX denoiser, adaptive sampling, persistent data between frames, capped light bounces (max_bounces 4).",
      "GPU backend chosen in order OptiX → CUDA → HIP → oneAPI → Metal → CPU fallback.",
      "Render: write rendered_frames/frame_####.png for the frame range. --first-frame renders one (lighting check); --stop-after-scene saves an editable .blend and skips rendering.",
    ],
    files: ["blender_stage.py · stage 2 (import → materials → world → camera → lights → render)", "enable_devices()"],
    inp: "alembic/motion_data.abc + config (camera, lights, world, preset)",
    out: "rendered_frames/frame_####.png",
    ui: "Builder: quality (samples/res), cam distance/angle/height, cam smoothing, keep-swarm-centered, origin marker, first-frame, prep-scene (-p). Camera move/look-at: config override only (no builder control yet). Jobs: render-scene / test-frame (-b).",
  },
  {
    n: "6",
    title: "Encode",
    tool: "ffmpeg  (host)",
    role: "Stitch the PNG sequence into a video at the run's fps.",
    what: [
      "run.py · run_ffmpeg() collects rendered_frames/frame_####.png in order and encodes an MP4 at the metadata fps.",
      "This is also what -s (stitch-only) runs by itself, e.g. after editing frames.",
    ],
    files: ["run.py · run_ffmpeg()"],
    inp: "rendered_frames/*.png + fps",
    out: "rendered_frames.mp4",
    ui: "Jobs/CLI: ‘stitch’ (-s) runs just this stage on an existing frame folder.",
  },
  {
    n: "7",
    title: "Web studio",
    tool: "FastAPI backend + React frontend  (this app)",
    role: "A thin control panel over the filesystem. The backend never re-implements the pipeline — it shells out to run.py and reads the output/ directory.",
    what: [
      "jobmanager.py: a single serial worker (one GPU, so one render at a time). It spawns  conda run --no-capture-output -n chrono python -u run.py …  as a detached process group, streams logs over SSE, and persists job state to jobs.json.",
      "runs.py: turns the output/ directory into the gallery — frame counts, thumbnails, video, the config that produced each run. Pure filesystem read.",
      "app.py: the HTTP routes (jobs, runs, frames, presets, open-folder) + serves the built frontend in the always-on deploy.",
      "Runs always-on as a systemd user service on :8780, reachable from any device over Tailscale.",
    ],
    files: ["web/backend/app.py, jobmanager.py, runs.py, presets.py", "web/frontend/src/*"],
    inp: "your clicks → run.py invocations",
    out: "jobs.json + everything in output/",
  },
];

const CAMERA = {
  role:
    "Where camera movement is planned. The camera is a first-class, data-driven spec — not hand-baked. resolve_camera() normalizes config into one spec; the build section interprets it into per-frame keyframes inside Blender.",
  modes: [
    ["static", "Fixed spherical placement (radius / azimuth / elevation). One transform, no animation."],
    ["track", "Hold a fixed offset & angle and translate with the look-at target each frame. The original clump-dolly."],
    ["orbit", "Sweep azimuth over the clip (turntable), with optional radius / elevation drift. Circles the target."],
    ["keyframes", "Authored waypoints {t, radius, azimuth, elevation, ease}, interpolated. Push-ins, cranes, reveals."],
  ],
  lookAt:
    "look_at is independent of the move: \"origin\" · \"clump\" (the densest swarm, robust to escaping bodies) · or a fixed [x,y,z]. So you can orbit WHILE keeping the swarm centered.",
  clump:
    "When look_at is \"clump\", the per-frame clump center is precomputed from the NPZ: median of all bodies, refined to the inner half, then a centered (lag-free) temporal smooth to de-shake.",
  config:
    '"camera_move": { "mode": "orbit", "orbit_degrees": 360, "radius": 50, "elevation": 20 },  "camera_look_at": "clump"',
  files: ["blender_stage.py · resolve_camera(), compute_clump_positions(), the Camera build block", "config.py · camera_move, camera_look_at, camera_lens_mm, camera_fstop"],
};

// How a click in the studio becomes pipeline input.
const CONTROL_PATH = [
  ["JobBuilder", "React form"],
  ["JobRequest", "JSON"],
  ["POST /api/jobs", "FastAPI"],
  ["build_args()", "jobmanager"],
  ["conda run … run.py …", "subprocess"],
  ["load_config()", "deep-merge"],
  ["stages", "physics → render"],
];

const FACTS = [
  ["Narrow waist", "Physics ↔ render only ever meet at motion_data.npz. That decoupling is what makes -ph (reuse physics) and -r (resume render) cheap."],
  ["Coordinate transform", "Chrono is Y-up, Blender is Z-up. Every position/rotation passes through map_pos / map_quat — a single 90° rotation about X — in one place."],
  ["GPU order", "OptiX → CUDA → HIP → oneAPI → Metal → CPU. First available wins (enable_devices)."],
  ["conda env", "Physics needs pychrono, which lives in the 'chrono' conda env. Jobs run via 'conda run -n chrono'. Blender uses its OWN bundled Python, not conda."],
  ["A run is self-describing", "output/outputN/ holds config_used.json, config_base.json, run_metadata.json, the NPZ, the Alembic, the exact blender_stage.py used, and all logs."],
  ["Render stage is static code", "blender_stage.py is a normal, lintable file (was a generated f-string). It reads the run's own files at startup instead of having values baked in."],
];

const FILE_MAP = [
  ["run.py", "orchestrator — modes, run dir, config resolution, ffmpeg"],
  ["config.py", "DEFAULT_CONFIG (the source of truth) + deep-merge"],
  ["physics.py", "Project Chrono sim → NPZ"],
  ["blender_driver.py", "find / launch Blender, stream logs (no codegen)"],
  ["blender_stage.py", "runs INSIDE Blender: build, bake, scene, camera, render"],
  ["web/backend/", "FastAPI: jobs queue, runs gallery, presets"],
  ["web/frontend/", "React studio (this UI)"],
];

function Stage({ s }) {
  return (
    <section className={s.contract ? "hiw-stage hiw-contract" : "hiw-stage"}>
      <div className="hiw-rail">
        <span className="hiw-num">{s.n}</span>
      </div>
      <div className="hiw-body">
        <h3 className="hiw-title">{s.title}</h3>
        <div className="hiw-tool">{s.tool}</div>
        <p className="hiw-role">{s.role}</p>
        <div className="hiw-section-label">What happens</div>
        <ul className="hiw-what">
          {s.what.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
        <div className="hiw-meta">
          <div>
            <span className="hiw-k">key files</span>
            {s.files.map((f, i) => (
              <code key={i} className="hiw-file">{f}</code>
            ))}
          </div>
          {(s.inp !== "—" || s.out !== "—") && (
            <div className="hiw-io">
              <span className="hiw-k">in</span> <span className="hiw-io-v">{s.inp}</span>
              <span className="hiw-arrow">→</span>
              <span className="hiw-k">out</span> <span className="hiw-io-v">{s.out}</span>
            </div>
          )}
          {s.ui && (
            <div className="hiw-ui">
              <span className="hiw-ui-tag">UI</span>
              <span>{s.ui}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export default function ArchDiagram() {
  return (
    <div className="hiw">
      <header className="hiw-head">
        <h2>How the pipeline works</h2>
        <p>
          A simulation is computed by Project Chrono, baked to a neutral motion file,
          then assembled and rendered inside Blender — orchestrated by <code>run.py</code> and
          driven from this studio. Top to bottom, here's exactly what runs and where.
        </p>
      </header>

      {/* data-flow ribbon */}
      <div className="hiw-flow">
        {FLOW.map(([name, sub], i) => (
          <Fragment key={name}>
            <div className="hiw-flow-node">
              <code>{name}</code>
              <span>{sub}</span>
            </div>
            {i < FLOW.length - 1 && <span className="hiw-flow-arrow">→</span>}
          </Fragment>
        ))}
      </div>

      {/* UI -> code wiring diagram */}
      <InputWiring />

      {/* stages */}
      <div className="hiw-stages">
        {STAGES.map((s) => (
          <Stage key={s.n} s={s} />
        ))}
      </div>

      {/* where the UI plugs in */}
      <section className="hiw-controls">
        <h3>Where the UI plugs in</h3>
        <p className="hiw-role">
          The studio never re-implements the pipeline — it just assembles a request and
          shells out to <code>run.py</code>. Two kinds of input: a few top-level fields
          become direct CLI flags; the advanced tunables are folded into a{" "}
          <code>config_override</code> (only the values that differ from defaults), written
          as JSON and deep-merged by <code>run.py</code>. The <span className="hiw-ui-tag">UI</span> line
          on each stage above shows which controls land there.
        </p>
        <div className="hiw-flow hiw-flow-tight">
          {CONTROL_PATH.map(([name, sub], i) => (
            <Fragment key={name}>
              <div className="hiw-flow-node">
                <code>{name}</code>
                <span>{sub}</span>
              </div>
              {i < CONTROL_PATH.length - 1 && <span className="hiw-flow-arrow">→</span>}
            </Fragment>
          ))}
        </div>
      </section>

      {/* camera deep-dive */}
      <section className="hiw-camera">
        <h3>Camera — where movement is planned</h3>
        <p className="hiw-role">{CAMERA.role}</p>
        <div className="hiw-section-label">Move modes</div>
        <ul className="hiw-modes">
          {CAMERA.modes.map(([m, d]) => (
            <li key={m}>
              <code className="hiw-mode">{m}</code>
              <span>{d}</span>
            </li>
          ))}
        </ul>
        <p className="hiw-note"><strong>look_at:</strong> {CAMERA.lookAt}</p>
        <p className="hiw-note"><strong>clump tracking:</strong> {CAMERA.clump}</p>
        <div className="hiw-section-label">Config</div>
        <pre className="hiw-code">{CAMERA.config}</pre>
        <div className="hiw-meta">
          <div>
            <span className="hiw-k">key files</span>
            {CAMERA.files.map((f, i) => (
              <code key={i} className="hiw-file">{f}</code>
            ))}
          </div>
        </div>
      </section>

      {/* cross-cutting facts */}
      <section className="hiw-facts">
        <h3>Things worth knowing</h3>
        <dl>
          {FACTS.map(([k, v]) => (
            <Fragment key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </Fragment>
          ))}
        </dl>
      </section>

      {/* file map */}
      <section className="hiw-facts">
        <h3>File map</h3>
        <dl className="hiw-filemap">
          {FILE_MAP.map(([f, d]) => (
            <Fragment key={f}>
              <dt><code>{f}</code></dt>
              <dd>{d}</dd>
            </Fragment>
          ))}
        </dl>
      </section>
    </div>
  );
}
