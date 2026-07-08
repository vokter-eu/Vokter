# Freeze the Vokter backend (desktop Phase 2)

Builds a self-contained `vokter-backend` bundle with PyInstaller — the user
installs nothing: Python and every native dep (SQLCipher, whisper, piper,
nostr) travel inside the bundle.

## Build (Linux)

```bash
# one-time: the desktop venv from ../setup.sh, plus PyInstaller
../runtime/venv/bin/pip install "pyinstaller>=6" pyinstaller-hooks-contrib

../runtime/venv/bin/pyinstaller --noconfirm vokter_backend.spec
```

Output: `dist/vokter-backend/` (~480 MB, git-ignored). PyInstaller does not
cross-compile — build on each target OS.

## Run

Configuration is env-driven (see `app/config.py` and `../orchestrator.py`);
the binary itself only reads `VOKTER_BIND` (default 127.0.0.1) and
`VOKTER_PORT` (default 8080). It can be launched from any directory.

```bash
VOKTER_DB=/path/to/data/vokter.db \
VOKTER_DB_KEY=... \
VOKTER_OLLAMA_URL=http://127.0.0.1:11435 \
VOKTER_PORT=8081 \
dist/vokter-backend/vokter-backend
```

## System requirements (validated)

Verified on a bare Ubuntu 24.04 container with NO network and none of the
dev dependencies installed: full E2E passes (boot, ingestion, RAG, TTS,
STT, encrypted DB, no-key refusal).

The bundle inherits the **glibc floor of the build host** (2.39 here →
binaries require glibc ≥ 2.38: Ubuntu 24.04+, Debian 13+, Fedora 39+).
Debian 12 (glibc 2.36) cannot load it. For release builds, build inside a
container of the oldest distro we want to support to widen compatibility.

## Notes

- `vokter_backend.spec` uses `collect_all` (never `collect_data_files`) for
  every package with native pieces — piper's `espeakbridge.so` and its
  `espeak-ng-data` must travel together or frozen synthesis breaks.
- Verified end-to-end frozen: UI, doc ingestion, RAG answer with citation,
  TTS (piper), STT (whisper), encrypted DB on disk.
- Ollama is NOT part of the bundle — `../setup.sh` provides it.
