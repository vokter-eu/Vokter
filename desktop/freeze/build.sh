#!/usr/bin/env bash
# Build the frozen Vokter backend, then prune data the voice engines can reach but our
# languages never use (unused espeak dicts, hf_xet, unused babel locales — see prune_frozen.py).
# The prune is part of the build on purpose: a bare `pyinstaller` re-includes everything, so the
# .deb only stays lean if this wrapper (not raw pyinstaller) produces dist/vokter-backend.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

../runtime/venv/bin/pyinstaller --noconfirm vokter_backend.spec
../runtime/venv/bin/python prune_frozen.py dist/vokter-backend/_internal
echo "freeze + prune complete → dist/vokter-backend"
