# Vokter Desktop — Phase 1 prototype

Goal of this phase: **prove the two heavy pieces (native Ollama + the FastAPI
backend) start together outside Docker, from one entry point.** No pretty UI, no
installer — those are later phases.

> Phase 1 validates **orchestration** (do the pieces boot together, no Docker),
> **not packaging** (can a user run it with zero installs). Packaging is Phase 2
> (freeze the backend into a self-contained executable). A green Phase 1 does
> **not** de-risk Phase 2.

## How to run

**1. Sudo bootstrap — run ONCE (needs root; installs the system libraries the
backend links against outside Docker, and kills the leftover Docker Ollama):**

```bash
sudo docker stop vokter-ollama 2>/dev/null || true
sudo apt install -y python3.12-venv ffmpeg libsqlcipher-dev
```

**2. Setup — no sudo (venv + native Ollama):**

```bash
./desktop/setup.sh
```

**3. Run the orchestrator:**

```bash
python3 desktop/orchestrator.py
```

First run downloads ~2 GB of models. When it says the backend is up, open
<http://127.0.0.1:8080> and send a chat message.

**4. Verify encryption is REAL (not a silent plaintext fallback):**

```bash
python3 desktop/verify_encryption.py
```

## The bundling manifest (a Phase-1 deliverable)

This is what Docker used to provide for us. Phase 2's frozen executable / Phase 4's
installer must reproduce it **inside the app** so the end user installs nothing:

| Piece | Provided today by | Why it's needed |
|-------|-------------------|-----------------|
| `python3.12-venv` | apt (dev box) | create the venv + pip (this box shipped no pip) |
| `ffmpeg` | apt (dev box) | faster-whisper decodes browser audio (WebM/OGG) |
| `libsqlcipher-dev` | apt (dev box) | real DB encryption (sqlcipher3 links against it) |
| native `ollama` | app-local download | the model runtime — replaces the Docker Ollama |
| Python deps | `app/requirements.txt` | fastapi, uvicorn, faster-whisper, piper-tts, sqlcipher3, nostr-sdk, mcp, … |

## Design notes

- **Native Ollama runs on port 11435**, not 11434 — the leftover Docker Ollama
  squats on 11434, and binding against it would make Phase 1 pass while proving
  nothing. Models are stored in the per-user writable data dir
  (`<data>/ollama-models`, dev: `runtime/data/ollama-models`), and Ollama's home
  (`<data>/ollama-home`) too — not in `~/.ollama` or the Docker volume.
- **DB key** is generated (strong, never the `change-me` default) and stored
  `0600` in `runtime/data/.db_key`. This is a Phase-1 shortcut; Phase 3 moves it
  to the OS keychain.
- `runtime/` is git-ignored — it holds the venv, the Ollama binary, models, the
  database and the key. Nothing machine-local is committed.
