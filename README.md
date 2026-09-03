# Vokter

**Your agent. Your data. By right.**

🌐 **[vokterai.com](https://vokterai.com)** — [Manifesto](https://vokterai.com/MANIFESTO.html) — [GitHub](https://github.com/vokter-eu/Vokter)

Vokter (Norwegian: *guardian*) is a personal, sovereign AI agent that runs on **your** machine. No third-party cloud, no accounts, no telemetry. It only knows what you teach it, and you can audit every line of code that makes it work.

> There's a right older than the internet: what is yours cannot be taken. Norwegians call it *odel*. Vokter is its digital guardian.
> — [Read the full manifesto](https://vokterai.com/MANIFESTO.html)

## What Vokter does today (v0.8.0)

| Capability | Details |
|---|---|
| **Document memory** | Ingest PDF, TXT, MD — chunked, embedded, stored locally. Full RAG answers with source citations. Real deletion (embeddings included). |
| **Encrypted database** | AES-256 at rest via SQLCipher. Set `VOKTER_DB_KEY` before first run. |
| **Local chat** | Conversation with your documents. Persistent history per session (SQLite-backed). Context window 8 192 tokens. |
| **Email connector** | IMAP/SSL — syncs and indexes your inbox locally. No cloud. |
| **Local voice** | Talk to Vokter: Whisper STT (faster-whisper) + Piper TTS. Your voice never leaves the machine. |
| **Web browsing** | Fetch and memorize web pages. Granular allowlist — you decide which domains Vokter can visit. |
| **Task planner** | Give Vokter a goal; it breaks it into steps (browse, ask), executes them, and streams the result back. |
| **Scheduled tasks** | Set recurring goals (every 5 m / 2 h / 1 d). Vokter runs them autonomously and stores the output. |
| **Agent personalisation** | Name, tone (formal/neutral/friendly), mode, language, model — all configurable at runtime without restarting Docker. |
| **Identity layer** | Master key + ephemeral session keys (HMAC-SHA256). Each external interaction gets a fresh, unlinkable key. |

## Quick start

### No-code install (recommended)

Download and double-click the installer for your OS — it sets everything up automatically:

- 🐧 **Linux** → [Download the .deb](https://github.com/vokter-eu/Vokter/releases/latest) (`sudo apt install ./vokter-desktop_*.deb`)
- 🍎 **macOS** → coming soon
- 🪟 **Windows** → coming soon

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free). The installer downloads the AI model, generates your encryption key, and opens Vokter at **http://localhost:8080** automatically.

### Manual install (developers)

```bash
git clone https://github.com/vokter-eu/Vokter.git
cd Vokter
cp .env.example .env
# Set VOKTER_DB_KEY in .env (generate with: openssl rand -hex 32)
docker compose up -d --build
docker exec -it vokter-ollama ollama pull llama3.2:3b
docker exec -it vokter-ollama ollama pull nomic-embed-text
```

Open **http://localhost:8080**. Upload a document and ask it anything. Not a single byte has left your machine.

### Hardware guide

| RAM | Recommended model |
|---|---|
| 8 GB | `llama3.2:3b` or `qwen2.5:3b` |
| 16 GB | `mistral` or `llama3.1:8b` |
| NVIDIA GPU | Uncomment the `deploy` block in `docker-compose.yml` |

## Configuration

All settings live in `.env` (copy from `.env.example`). The most important ones:

| Variable | Default | Description |
|---|---|---|
| `VOKTER_DB_KEY` | *(must change)* | Database encryption passphrase |
| `VOKTER_CHAT_MODEL` | `llama3.2:3b` | Ollama model for chat and planning |
| `VOKTER_EMBED_MODEL` | `nomic-embed-text` | Ollama model for embeddings |

See `.env.example` for the full list with comments.

## Roadmap

- [x] **Phase 1 — Your agent on your machine**: local LLM via Ollama, AES-256 encrypted memory, RAG chat, email connector.
- [x] **Phase 2 — Your agent goes out into the world**: 100% local voice (Whisper STT + Piper TTS), web browsing with allowlist permissions, multi-step task planner with SSE streaming, identity layer.
- [x] **Phase 4 — Your agent works while you sleep**: scheduled background tasks with configurable intervals, run history, autonomous execution via the planner pipeline.
- [x] **Phase 5 — Make it yours**: agent personalisation (name, tone, language, model selection, conversation history). Docker-first setup with `.env.example`. SQLCipher encryption active in Docker.
- [x] **Phase 6 — Your agent talks to other agents**: MCP server adapter (connect to Claude Desktop and other MCP hosts), Nostr adapter (DMs as tool calls, identity derived from master key).
- [ ] **Phase 7 — Sovereign cloud compute (optional)**: for users without local hardware (mobile, old machines), opt-in confidential compute via TEE (Intel TDX / AMD SEV-SNP) — used without an account or identity. Remote attestation gives users cryptographic proof of privacy before sending anything. No trust required: the hardware itself signs the guarantee.
- [ ] **Phase 8 — Vokter Infrastructure**: own European datacenter with confidential compute hardware. Third-party security audits published openly. Operator (Vokter) is architecturally unable to access user data — verifiable, not promised.

## Non-negotiable principles

1. **Local first.** By default, everything is processed on your hardware.
2. **Zero hidden calls.** No requests to third-party AI APIs. Check the code — that's the point.
3. **Your keys, yours alone.** They never leave your device — what is yours cannot be taken.
4. **Real deletion.** Delete means delete, embeddings included.
5. **Open source.** We don't ask for trust; we give proof.
6. **For your life, not to retain you.** Vokter gives you back time and pushes you toward your real life. No engagement mechanics, ever.

## Security

- Database encrypted at rest with AES-256 (SQLCipher). Set `VOKTER_DB_KEY` — required, no fallback.
- Runs as a non-root user inside Docker.
- Web browsing is allowlist-only — Vokter cannot visit a domain you haven't explicitly permitted, and redirects to private/internal addresses are blocked.
- No data is ever sent to a third-party service unless you configure an external connector (e.g. an IMAP mailbox you own).

## License

AGPL-3.0 — free forever, and improvements flow back to the community.

## Community

- Discussions: the Discussions tab of this repository
- [Contribute](CONTRIBUTING.md)

---

*Vokter is an independent European project. Not affiliated with any big tech, and that's exactly the point.*
