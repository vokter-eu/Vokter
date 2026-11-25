#!/usr/bin/env bash
# Build the frozen Vokter backend, then prune data the voice engines can reach but our
# languages never use (unused espeak dicts, hf_xet, unused babel locales — see prune_frozen.py).
# The prune is part of the build on purpose: a bare `pyinstaller` re-includes everything, so the
# .deb only stays lean if this wrapper (not raw pyinstaller) produces dist/vokter-backend.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# The frozen bundle inherits the glibc of the BUILD host (see README.md). Building on a host newer
# than the floor ships binaries that import-fail on clean target systems (the voice/numpy bug that a
# dev host at 2.43 produced) — so the floor is ENFORCED, not just documented. Release builds happen
# in a clean Ubuntu 24.04 VM (vokter-internal/RELEASE_BUILD_METHOD.md).
GLIBC_FLOOR="2.39"
check_glibc_floor() {
  local host_glibc
  host_glibc="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
  if [ "$(printf '%s\n' "$GLIBC_FLOOR" "$host_glibc" | sort -V | tail -n1)" != "$GLIBC_FLOOR" ]; then
    echo "ERROR: build host glibc is ${host_glibc}, above the release floor ${GLIBC_FLOOR}." >&2
    echo "A bundle frozen here will NOT run on clean Ubuntu 24.04 (glibc ${GLIBC_FLOOR})." >&2
    echo "Build inside the clean 24.04 VM instead (vokter-internal/RELEASE_BUILD_METHOD.md)." >&2
    return 1
  fi
  echo "glibc floor check OK: host ${host_glibc} <= floor ${GLIBC_FLOOR}"
}
if [ "${1:-}" = "--check-glibc-only" ]; then check_glibc_floor; exit $?; fi
check_glibc_floor

../runtime/venv/bin/pyinstaller --noconfirm vokter_backend.spec
../runtime/venv/bin/python prune_frozen.py dist/vokter-backend/_internal
echo "freeze + prune complete → dist/vokter-backend"
