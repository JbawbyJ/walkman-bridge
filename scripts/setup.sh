#!/bin/sh
# Walkman Bridge - one-time setup (Linux / macOS). Safe to re-run.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo ""
echo "=== WALKMAN BRIDGE - one-time setup ==="
echo "    Project: $ROOT"
echo ""

# pick a Python
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[!] Python 3 not found. Install it (https://www.python.org) and re-run." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[!] Node.js / npm not found. Install Node LTS (https://nodejs.org) and re-run." >&2
  exit 1
fi

# Python environment
if [ -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "[OK] Python environment already exists - skipping creation."
else
  echo "[..] Creating Python environment (backend/.venv) ..."
  "$PY" -m venv "$ROOT/backend/.venv"
fi

echo "[..] Installing Python packages ..."
"$ROOT/backend/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$ROOT/backend/requirements.txt"
echo "[OK] Python packages installed."

# Frontend (dashboard)
if [ -f "$ROOT/frontend/dist/index.html" ]; then
  echo "[OK] Dashboard already built - skipping."
else
  echo "[..] Installing dashboard packages and building (this can take a minute) ..."
  ( cd "$ROOT/frontend" && npm install && npm run build )
  echo "[OK] Dashboard built."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo " Next steps:"
echo "  1. Put the JSymphonic program file (the .jar) at:"
echo "       $ROOT/backend/vendor/jsymphonic.jar"
echo "     Without it the app still starts, but music transfers will not work."
if [ -f "$ROOT/backend/vendor/jsymphonic.jar" ]; then
  echo "     [OK] Good news: jsymphonic.jar is already there."
else
  echo "     [!] It is not there yet."
fi
echo "  2. Run scripts/start.sh to launch the dashboard."
echo ""
