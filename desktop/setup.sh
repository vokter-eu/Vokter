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
echo "[setup] installing backend dependencies from the LOCKFILE (this compiles native wheels)…"
# Install the FULLY PINNED lockfile (app/requirements.lock), NOT the loose direct-deps list
# (app/requirements.txt). The lock freezes the entire transitive tree so a release build gets EXACTLY
# the versions that were validated — no transitive drift (the class of bug that bit nostr-sdk, Ollama,
# and PyAV/av). --require-hashes makes pip verify every artifact's sha256 against the lock, so a
# tampered or substituted package ABORTS the install (versions alone don't protect the bytes).
# To change deps: edit requirements.txt, recreate the venv from it, then regenerate the lock with
# `uv pip compile <frozen pins> --generate-hashes --python-version 3.12`.
"$RUNTIME/venv/bin/python" -m pip install --require-hashes -r "$REPO/app/requirements.lock"

# --- 2. Native Ollama (app-local, no sudo) ----------------------------------
# Ollama ships as a .tar.zst on GitHub releases (asset: ollama-linux-<arch>.tar.zst).
# PINNED to an EXACT version (not "latest"): a core runtime must be reproducible — an unpinned fetch
# drifts/breaks on any upstream change (same lesson as the nostr-sdk==0.44.2 pin). 0.32.5 is the version
# currently shipping that passed all tests. Bump this deliberately + re-validate; the tagged-release URL
# is deterministic, so no GitHub API lookup is needed.
OLLAMA_VERSION="0.32.5"
# PINNED sha256 per arch (from the v0.32.5 release's official sha256sum.txt, captured 2026-11-25 and
# hardcoded HERE — never fetched at install time, or whoever can swap the tarball can swap the sums
# too). Same verify-the-bytes rule the voice packs and GGUF pulls already follow. On a version bump,
# update BOTH the version and these hashes.
case "$(uname -m)" in
  x86_64|amd64)  OLLAMA_ARCH="amd64"; OLLAMA_SHA256="f7d6bdbcf71b83aa8670c4e7dc4b6936c0952fcf8b114eaf6a11cbadb9684214" ;;
  aarch64|arm64) OLLAMA_ARCH="arm64"; OLLAMA_SHA256="aa7e06b5683ee66c4a3ec68ea7236db43b5a5d0821f0dfe2c5a215f4462bddf4" ;;
  *) echo "[setup] unsupported arch $(uname -m)"; exit 1 ;;
esac
ASSET="ollama-linux-${OLLAMA_ARCH}.tar.zst"
OLLAMA_URL="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/${ASSET}"
echo "[setup] downloading native Ollama v${OLLAMA_VERSION} ($ASSET) → $RUNTIME/ollama"
mkdir -p "$RUNTIME/ollama"
curl -L --fail "$OLLAMA_URL" -o "$RUNTIME/ollama.tar.zst"
echo "${OLLAMA_SHA256}  $RUNTIME/ollama.tar.zst" | sha256sum -c - || {
  echo "[setup] ERROR: Ollama tarball sha256 MISMATCH — refusing to extract (tampered or corrupted download)." >&2
  rm -f "$RUNTIME/ollama.tar.zst"
  exit 1
}
tar --zstd -xf "$RUNTIME/ollama.tar.zst" -C "$RUNTIME/ollama"
rm -f "$RUNTIME/ollama.tar.zst"

echo "[setup] done. Verify the binary:"
"$RUNTIME/ollama/bin/ollama" --version || true
echo "[setup] next: python3 desktop/orchestrator.py"
