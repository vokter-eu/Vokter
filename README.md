# Vokter

**Your agent. Your data. Your money. By right.**

Vokter (Norwegian: *guardian*) is a personal, sovereign AI agent that runs on **your** machine. No third-party cloud, no accounts, no telemetry. It only knows what you teach it, and you can audit every line of code that makes it work.

> There's a right older than the internet: what is yours cannot be taken. Norwegians call it *odel*. Vokter is its digital guardian.
> — [Read the full manifesto](docs/MANIFESTO.md)

## What Vokter does today (v0.6.0)

| Capability | Details |
|---|---|
| **Document memory** | Ingest PDF, TXT, MD — chunked, embedded, and stored locally. Full RAG answers with source citations. Real deletion (embeddings included). |
| **Encrypted database** | AES-256 at rest via SQLCipher. Set `VOKTER_DB_KEY` before first run. |
| **Local chat** | Conversation with your documents. Context window 8 192 tokens, per-session memory. |
| **Email connector** | IMAP/SSL — syncs and indexes your inbox locally. No cloud. |
| **Local voice** | Talk to Vokter: Whisper STT (faster-whisper, CPU) + Piper TTS. Your voice never leaves the machine. |
| **Web browsing** | Fetch and memorize web pages. Granular allowlist — you decide which domains Vokter can visit. |
| **Task planner** | Give Vokter a goal; it breaks it into steps (browse, ask), executes them, and streams the result back. |
| **Wallet** | Cashu e-cash (fully functional). Pluggable adapters for Lightning, EURC/EURe/EURCV, Solana, Monero, Bitcoin. Human confirmation and daily spend limits always enforced. |
| **Scheduled tasks** | Set recurring goals (every 5 m / 2 h / 1 d). Vokter runs them autonomously and stores the output. |
| **Identity layer** | Master key + ephemeral session keys (HMAC-SHA256). Each external interaction gets a fresh, unlinkable key. |

## Quick start

Requirements: Docker and Docker Compose. Recommended: 8 GB RAM minimum.

```bash
git clone https://github.com/vokter-eu/Vokter.git
cd Vokter/docker
```

Edit `docker-compose.yml` — **change `VOKTER_DB_KEY`** to a strong passphrase before anything else.

```bash
docker compose up -d --build
docker exec -it vokter-ollama ollama pull llama3.2:3b
docker exec -it vokter-ollama ollama pull nomic-embed-text
```

Open **http://localhost:8080**. Upload a document and ask it anything. Not a single byte has left your machine.

### Hardware guide

| RAM | Recommended model |
|---|---|
| 8 GB | `llama3.2:3b` or `qwen2.5:3b` |
| 16 GB | `llama3.2:3b`, `mistral`, or `gemma2:9b` |
| NVIDIA GPU | Uncomment the `deploy` block in `docker-compose.yml` |

## Roadmap

- [x] **Phase 1 — Your agent on your machine**: local LLM via Ollama, AES-256 encrypted memory, RAG chat, email connector.
- [x] **Phase 2 — Your agent goes out into the world**: 100% local voice (Whisper STT + Piper TTS), web browsing with allowlist permissions, multi-step task planner with SSE streaming, identity layer.
- [x] **Phase 3 — Your agent pays**: non-custodial wallet, Cashu e-cash, pluggable adapter architecture (Lightning, MiCA stablecoins, Monero, Bitcoin). Human confirmation and spend limits always.
- [x] **Phase 4 — Your agent works while you sleep**: scheduled background tasks with configurable intervals, run history, autonomous execution via the planner pipeline.
- [ ] **Phase 5 — Make it yours**: personalisation (agent name, avatar, tone, language, model selection) and a self-serve distribution web so anyone can stand up their own Vokter without touching a terminal.

## Non-negotiable principles

1. **Local first.** By default, everything is processed on your hardware.
2. **Zero hidden calls.** No requests to third-party AI APIs. Check the code — that's the point.
3. **Your keys, your money.** Non-custodial or nothing.
4. **Real deletion.** Delete means delete, embeddings included.
5. **Open source.** We don't ask for trust; we give proof.
6. **For your life, not to retain you.** Vokter gives you back time and pushes you toward your real life. No engagement mechanics, ever.

## Security

- Database encrypted at rest with AES-256 (SQLCipher). Set `VOKTER_DB_KEY`.
- Runs as a non-root user inside Docker.
- Web browsing is allowlist-only — Vokter cannot visit a domain you haven't explicitly permitted, and redirects to private/internal addresses are blocked.
- All payments require explicit user confirmation (`confirmed: true`). Daily spend limits enforced in the route layer.
- No data is ever sent to a third-party service unless you configure an external adapter (e.g. an LNbits instance you run yourself).

## License

AGPL-3.0 — free forever, and improvements flow back to the community.

## Community

- Discussions: the Discussions tab of this repository
- [Contribute](CONTRIBUTING.md)

---

*Vokter is an independent European project. Not affiliated with any big tech, and that's exactly the point.*
