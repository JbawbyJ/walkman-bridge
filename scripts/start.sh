#!/bin/sh
# Walkman Bridge - start the server and open the dashboard (Linux / macOS).
# Ctrl+C stops the server.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -x "$ROOT/backend/.venv/bin/python" ] || [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "[!] The app is not set up yet (backend/.venv or frontend/dist is missing)."
  echo "    Run scripts/setup.sh first, then try again."
  exit 1
fi

# resolve Java: env override -> portable JDK in ../tools -> PATH
JAVA_BIN=""
if [ -n "$WALKMAN_BRIDGE_JAVA" ]; then
  JAVA_BIN="$WALKMAN_BRIDGE_JAVA"
else
  for j in "$ROOT/.."/tools/jdk*/bin/java; do
    [ -x "$j" ] && JAVA_BIN="$j"
  done
  if [ -z "$JAVA_BIN" ] && command -v java >/dev/null 2>&1; then
    JAVA_BIN="java"
  fi
fi

if [ -n "$JAVA_BIN" ]; then
  export WALKMAN_BRIDGE_JAVA="$JAVA_BIN"
  echo "[i] Java: $JAVA_BIN"
else
  echo "[!] No Java found. Music transfers to the Walkman will not work."
  echo "    Install a JDK (e.g. Temurin 21) or place one in ../tools/jdk*/"
fi

if [ ! -f "$ROOT/backend/vendor/jsymphonic.jar" ]; then
  echo "[!] backend/vendor/jsymphonic.jar is missing."
  echo "    The dashboard will still open and show your Walkman's status,"
  echo "    but music transfers will NOT work until the jar is added."
fi

echo "[i] Starting Walkman Bridge at http://127.0.0.1:8000  (Ctrl+C to stop)"

# open the browser once the server has had a moment to come up
(
  sleep 2
  if command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:8000"        # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:8000"    # Linux
  fi
) >/dev/null 2>&1 &

cd "$ROOT/backend"
. "$ROOT/backend/.venv/bin/activate"
exec "$ROOT/backend/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port 8000
