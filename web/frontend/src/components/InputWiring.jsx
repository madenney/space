import { Fragment, useLayoutEffect, useRef, useState } from "react";

// Wiring diagram: every studio input on the left, the exact place in the script
// structure it's consumed on the right, with measured SVG arrows between them.
// Hover an input (or a target) to isolate its connections.

// Right column — concrete code locations the inputs feed into.
const TARGETS = [
  { id: "modes", label: "run.py · mode dispatch", sub: "new / -r / -ph / -s / -p" },
  { id: "sim", label: "physics.py · run_chrono_sim()", sub: "spawn + N-body gravity loop" },
  { id: "preset", label: "config preset", sub: "{ samples, res, fps, hz }" },
  { id: "camresolve", label: "blender_stage.py · resolve_camera()", sub: "normalize the camera spec" },
  { id: "render", label: "blender_stage.py · render stage", sub: "scene import · samples/res · render() · scene save" },
  { id: "world", label: "blender_stage.py · world / lights / marker", sub: "scene dressing" },
  { id: "ffmpeg", label: "run.py · run_ffmpeg()", sub: "stitch frames → mp4" },
];

// Left column — the actual UI controls, grouped as in the builder.
const GROUPS = ["basics", "physics", "camera", "render", "actions"];
const INPUTS = [
  // quality selects the preset, which then drives physics cadence (fps/hz),
  // the render (samples/res), and the encode framerate — so it fans out widely.
  { id: "quality", label: "quality", group: "basics", to: ["preset", "sim", "render", "ffmpeg"] },
  { id: "bodies", label: "bodies", group: "basics", to: ["sim"] },
  { id: "duration", label: "duration", group: "basics", to: ["sim"] },
  { id: "gravity", label: "gravity", group: "physics", to: ["sim"] },
  { id: "gravsoft", label: "gravity softening", group: "physics", to: ["sim"] },
  { id: "move", label: "move speed", group: "physics", to: ["sim"] },
  { id: "spin", label: "spin speed", group: "physics", to: ["sim"] },
  { id: "camdist", label: "cam distance", group: "camera", to: ["camresolve"] },
  { id: "camang", label: "cam angle", group: "camera", to: ["camresolve"] },
  { id: "camh", label: "cam height", group: "camera", to: ["camresolve"] },
  { id: "camsm", label: "cam smoothing", group: "camera", to: ["camresolve"] },
  { id: "track", label: "keep swarm centered", group: "camera", to: ["camresolve"] },
  { id: "cammove", label: "camera move / look-at *", group: "camera", to: ["camresolve"], dashed: true },
  { id: "origin", label: "origin marker", group: "camera", to: ["world"] },
  { id: "first", label: "first frame only", group: "render", to: ["render"] },
  { id: "prep", label: "prep & edit in Blender", group: "render", to: ["render"] },
  { id: "reuse", label: "reuse physics", group: "actions", to: ["modes"] },
  { id: "resume", label: "resume render", group: "actions", to: ["modes"] },
  { id: "scene", label: "render scene / test frame", group: "actions", to: ["render"] },
];

export default function InputWiring() {
  const containerRef = useRef(null);
  const nodes = useRef({}); // id -> element
  const [paths, setPaths] = useState([]);
  const [hover, setHover] = useState(null);

  useLayoutEffect(() => {
    const compute = () => {
      const c = containerRef.current;
      if (!c) return;
      const cb = c.getBoundingClientRect();
      const next = [];
      for (const inp of INPUTS) {
        const a = nodes.current[`in-${inp.id}`];
        if (!a) continue;
        const ab = a.getBoundingClientRect();
        const x1 = ab.right - cb.left;
        const y1 = ab.top - cb.top + ab.height / 2;
        for (const t of inp.to) {
          const b = nodes.current[`out-${t}`];
          if (!b) continue;
          const bb = b.getBoundingClientRect();
          const x2 = bb.left - cb.left;
          const y2 = bb.top - cb.top + bb.height / 2;
          const dx = Math.max(40, (x2 - x1) * 0.5);
          next.push({
            id: `${inp.id}-${t}`,
            inp: inp.id,
            out: t,
            dashed: inp.dashed,
            d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
          });
        }
      }
      setPaths(next);
    };
    compute();
    const ro = new ResizeObserver(compute);
    if (containerRef.current) ro.observe(containerRef.current);
    // Recompute once more after fonts settle.
    const t = setTimeout(compute, 250);
    window.addEventListener("resize", compute);
    return () => {
      ro.disconnect();
      clearTimeout(t);
      window.removeEventListener("resize", compute);
    };
  }, []);

  const hoveredInput = hover && INPUTS.find((i) => i.id === hover);
  const inputHot = (i) =>
    hover === i.id || (hover && TARGETS.some((t) => t.id === hover) && i.to.includes(hover));
  const targetHot = (t) =>
    hover === t.id || (hoveredInput && hoveredInput.to.includes(t.id));

  return (
    <section className="wire-wrap">
      <h3>Input wiring — UI → code</h3>
      <p className="hiw-role">
        Every control the studio exposes (left) and the exact place in the script it's
        consumed (right). Hover any row to isolate its path.
      </p>
      <div className="wire" ref={containerRef}>
        <svg className="wire-svg">
          <defs>
            <marker id="wire-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
            </marker>
          </defs>
          {paths.map((p) => {
            const hot = hover && (hover === p.inp || hover === p.out);
            const dim = hover && !hot;
            return (
              <path
                key={p.id}
                d={p.d}
                className={`wire-line${hot ? " hot" : ""}${dim ? " dim" : ""}${p.dashed ? " dashed" : ""}`}
                markerEnd="url(#wire-arrow)"
              />
            );
          })}
        </svg>

        <div className="wire-col wire-in">
          {GROUPS.map((g) => (
            <Fragment key={g}>
              <div className="wire-group">{g}</div>
              {INPUTS.filter((i) => i.group === g).map((i) => (
                <div
                  key={i.id}
                  ref={(el) => (nodes.current[`in-${i.id}`] = el)}
                  className={`wire-node${inputHot(i) ? " hot" : ""}${hover && !inputHot(i) ? " dim" : ""}`}
                  onMouseEnter={() => setHover(i.id)}
                  onMouseLeave={() => setHover(null)}
                >
                  {i.label}
                </div>
              ))}
            </Fragment>
          ))}
        </div>

        <div className="wire-col wire-out">
          {TARGETS.map((t) => (
            <div
              key={t.id}
              ref={(el) => (nodes.current[`out-${t.id}`] = el)}
              className={`wire-node out${targetHot(t) ? " hot" : ""}${hover && !targetHot(t) ? " dim" : ""}`}
              onMouseEnter={() => setHover(t.id)}
              onMouseLeave={() => setHover(null)}
            >
              <code>{t.label}</code>
              {t.sub && <span>{t.sub}</span>}
            </div>
          ))}
        </div>
      </div>
      <p className="hiw-note">
        * <strong>camera move / look-at</strong> reaches <code>resolve_camera()</code> through a
        config override only — there's no builder control for it yet (dashed line).
      </p>
    </section>
  );
}
