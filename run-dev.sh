#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

cleanup() {
  local exit_code=$?

  for pid in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
      wait "${pid}" 2>/dev/null || true
    fi
  done

  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

(
  uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8050
) &
BACKEND_PID=$!
echo "Backend started (PID ${BACKEND_PID})"

(
  cd frontend
  npm run dev -- --host
) &
FRONTEND_PID=$!
echo "Frontend started (PID ${FRONTEND_PID})"

wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
echo "One of the processes exited, shutting down the remainder..."
