import { useEffect } from "react";

// Edit this list freely — just the Blender hotkeys you actually use.
// Each group: { title, keys: [[combo, what it does], ...] }
const HOTKEYS = [
  {
    title: "Camera",
    keys: [
      ["Numpad 0", "toggle in/out of camera view"],
      ["Ctrl + Numpad 0", "make selected object the active camera + jump to it"],
      ["N → View → Lock Camera to View", "orbit/pan moves the camera itself (compose your shot)"],
    ],
  },
  {
    title: "Navigation",
    keys: [
      ["Numpad . (period)", "frame/zoom to selected object"],
      ["Home", "frame the whole scene"],
      ["Middle-mouse drag", "orbit"],
      ["Shift + Middle-mouse", "pan"],
    ],
  },
  {
    title: "Lights & objects",
    keys: [
      ["G", "grab/move (then X / Y / Z to lock axis)"],
      ["R", "rotate"],
      ["S", "scale"],
      ["Shift + A", "add object (lights, camera, etc.)"],
    ],
  },
  {
    title: "Save",
    keys: [
      ["Ctrl + S", "save the .blend (keep the same name, then hit 'test frame')"],
    ],
  },
];

export default function HotkeysModal({ onClose }) {
  // Close on Escape.
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Blender hotkeys</h3>
          <button className="modal-close" onClick={onClose} aria-label="close">
            ✕
          </button>
        </div>
        <div className="modal-body">
          {HOTKEYS.map((group) => (
            <div key={group.title} className="hk-group">
              <div className="hk-title">{group.title}</div>
              {group.keys.map(([combo, desc]) => (
                <div key={combo} className="hk-row">
                  <kbd className="hk-key">{combo}</kbd>
                  <span className="hk-desc">{desc}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="modal-foot">esc or click outside to close</div>
      </div>
    </div>
  );
}
