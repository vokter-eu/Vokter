#!/usr/bin/env bash
# Vokter desktop — Phase 1 setup (NO sudo needed; run AFTER the sudo bootstrap).
#
# The sudo bootstrap (run once, by the human) installs the system libraries the
# backend links against outside Docker:
#     sudo docker stop vokter-ollama 2>/dev/null || true   # kill the ghost Ollama
#     sudo apt install -y python3.12-venv ffmpeg libsqlcipher-dev
#
# This script then does everything that does NOT need root:
#   1. a Python venv with the backend's dependencies
#   2. a NATIVE, app-local Ollama binary (no system install, no Docker)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HERE/runtime"
REPO="$(dirname "$HERE")"
mkdir -p "$RUNTIME"

# --- 1. Backend venv --------------------------------------------------------
echo "[setup] creating Python venv → $RUNTIME/venv"
python3 -m venv "$RUNTIME/venv"
"$RUNTIME/venv/bin/python" -m pip install --upgrade pip
echo "[setup] installing backend dependencies (this compiles native wheels)…"
"$RUNTIME/venv/bin/python" -m pip install -r "$REPO/app/requirements.txt"

# --- 2. Native Ollama (app-local, no sudo) ----------------------------------
# Ollama ships as a .tar.zst on GitHub releases (asset: ollama-linux-<arch>.tar.zst).
# PINNED to an EXACT version (not "latest"): a core runtime must be reproducible — an unpinned fetch
# drifts/breaks on any upstream change (same lesson as the nostr-sdk==0.44.2 pin). 0.32.5 is the version
# currently shipping that passed all tests. Bump this deliberately + re-validate; the tagged-release URL
# is deterministic, so no GitHub API lookup is needed.
OLLAMA_VERSION="0.32.5"
case "$(uname -m)" in
  x86_64|amd64)  OLLAMA_ARCH="amd64" ;;
  aarch64|arm64) OLLAMA_ARCH="arm64" ;;
  *) echo "[setup] unsupported arch $(uname -m)"; exit 1 ;;
esac
ASSET="ollama-linux-${OLLAMA_ARCH}.tar.zst"
OLLAMA_URL="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/${ASSET}"
echo "[setup] downloading native Ollama v${OLLAMA_VERSION} ($ASSET) → $RUNTIME/ollama"
mkdir -p "$RUNTIME/ollama"
curl -L --fail "$OLLAMA_URL" -o "$RUNTIME/ollama.tar.zst"
tar --zstd -xf "$RUNTIME/ollama.tar.zst" -C "$RUNTIME/ollama"
rm -f "$RUNTIME/ollama.tar.zst"

echo "[setup] done. Verify the binary:"
"$RUNTIME/ollama/bin/ollama" --version || true
echo "[setup] next: python3 desktop/orchestrator.py"
