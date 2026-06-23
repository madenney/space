#!/usr/bin/env bash
# Install the Space studio (FastAPI API + built web UI) as a systemd *user*
# service so this machine brings the dashboard up on boot and keeps it alive.
# Reach it from any tailnet device at http://<this-machine>:8780
#
# Run as your normal user (NOT root):  ./scripts/install-service.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$REPO_ROOT/deploy/space-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_DST="$UNIT_DIR/space-web.service"
VENV_UVICORN="$REPO_ROOT/web/backend/.venv/bin/uvicorn"
DIST="$REPO_ROOT/web/frontend/dist"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing $UNIT_SRC" >&2; exit 1
fi
if [[ ! -x "$VENV_UVICORN" ]]; then
  echo "No uvicorn at $VENV_UVICORN — create web/backend/.venv and install deps first." >&2; exit 1
fi
if [[ ! -d "$DIST" ]]; then
  echo "No built frontend at $DIST — run: (cd web/frontend && npm run build)" >&2; exit 1
fi

mkdir -p "$UNIT_DIR"
# Render jobs run `conda run -n chrono …`, so conda must be on the service PATH.
CONDA_BASE="$(conda info --base 2>/dev/null || echo "$HOME/Tools/anaconda3")"
if [[ ! -x "$CONDA_BASE/condabin/conda" && ! -x "$CONDA_BASE/bin/conda" ]]; then
  echo "WARNING: no conda found at $CONDA_BASE — render jobs will fail until PATH is fixed." >&2
fi
# Rewrite %h/Projects/space -> repo, and __CONDA_BASE__ -> this machine's conda.
sed -e "s#%h/Projects/space#$REPO_ROOT#g" -e "s#__CONDA_BASE__#$CONDA_BASE#g" "$UNIT_SRC" > "$UNIT_DST"
echo "Wrote $UNIT_DST (WorkingDirectory=$REPO_ROOT/web/backend, conda=$CONDA_BASE)"

# Let user services run without an active login session (survives logout/reboot).
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" 2>/dev/null && echo "Enabled linger for $USER" \
    || echo "Could not enable linger (may need: sudo loginctl enable-linger $USER)"
fi

systemctl --user daemon-reload
systemctl --user enable --now space-web.service
echo
systemctl --user --no-pager status space-web.service || true
echo
echo "Done. Studio: http://$(hostname):8780  (reach it from anywhere via Tailscale)"
echo "Logs:    journalctl --user -u space-web -f"
echo "Restart: systemctl --user restart space-web"
echo "Rebuild UI after frontend changes: (cd web/frontend && npm run build)"
