# Vokter

**Your agent. Your data. By right.**

🌐 **[vokterai.com](https://vokterai.com)** — [Manifesto](https://vokterai.com/MANIFESTO.html) — [GitHub](https://github.com/vokter-eu/Vokter)

Vokter (Norwegian: *guardian*) is a personal, sovereign AI agent that runs on **your** machine. No third-party cloud, no accounts, no telemetry. It only knows what you teach it, and you can audit every line of code that makes it work.

> There's a right older than the internet: what is yours cannot be taken. Norwegians call it *odel*. Vokter is its digital guardian.
> — [Read the full manifesto](https://vokterai.com/MANIFESTO.html)

## What Vokter does today (v0.14.0)

| Capability | Details |
|---|---|
| **Document memory** | Ingest PDF, TXT, MD — chunked, embedded (bge-m3), stored locally. RAG answers with source citations. Real deletion (embeddings included). |
| **Local chat** | Streamed conversation with your documents and memory. Persistent per-session history (SQLite-backed). |
| **On-device voice** | Whisper speech-to-text + local text-to-speech — Kokoro (English, Spanish, French, Italian, Portuguese) and downloadable Piper voice packs (German, Dutch, Catalan). Your voice never leaves the machine. |
| **In-app model management** | Pick and download chat models from inside the app, with a live progress bar and an active-model badge. Vokter reads your hardware and recommends a model — no config files. |
| **Languages** | English, Spanish, French, German, Italian, Portuguese, Dutch, and Catalan (via the Salamandra model). |
| **Email connector** | IMAP/SSL — syncs and indexes your inbox locally; drafts replies, never sends without your confirmation. |
| **Web browsing** | Fetch and memorize web pages. Allow-list only — you decide which domains Vokter may visit; redirects to private/internal addresses are blocked. |
| **Task planner** | Give Vokter a goal; it breaks it into steps, executes them, and streams the result back. |
| **Scheduled tasks** | Set recurring goals; Vokter runs them autonomously and stores the output. |
| **Encrypted at rest** | AES-256 via SQLCipher. The key is generated on first run and held in your OS keyring — it never leaves the machine. |
| **Agent interop** | Talk to other agents over MCP (e.g. Claude Desktop) and Nostr, with ephemeral, unlinkable session keys per interaction. |

## Quick start

Vokter ships as a desktop app — no Docker, no accounts, no config files.

### Linux (available now)

1. Download the latest `.deb` from **[github.com/vokter-eu/Vokter/releases/latest](https://github.com/vokter-eu/Vokter/releases/latest)**.
2. Install it:
   ```bash
   sudo apt install ./vokter-desktop_*.deb
   ```
3. Launch **Vokter** from your applications menu.

On first run, Vokter downloads its models — the **qwen2.5:3b** chat model and the **bge-m3** multilingual embedder (~2.4 GB) — and generates your encryption key automatically. Everything after that runs locally: no account, no cloud, no telemetry. Not a single byte leaves your machine.

**macOS** and **Windows** builds are coming soon. In the meantime the source is public — see *Build from source* below.

### Choosing a model

Vokter detects your hardware and recommends a model; you can pick and download others from the in-app model manager at any time:

| Your machine | Recommended model | Download |
|---|---|---|
| Most laptops / weaker CPU | `qwen2.5:3b` — *Light* (the default) | ~2 GB |
| Capable CPU or GPU | `gemma3:4b` — *Balanced* | ~3 GB |
| Strong GPU | `qwen3:30b-a3b` — *Powerful* | ~18 GB |
| Catalan | `salamandra-2b-instruct` | ~1.5 GB |

The recommendation is conservative on purpose: a bigger model is only suggested when your hardware can actually run it responsively.

### Build from source (developers)

Vokter is fully open source (AGPL-3.0). Clone the repo and build the desktop app from `desktop/` — a PyInstaller-frozen Python backend wrapped in an Electron shell. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

There are no config files or API keys. Everything lives in the app's **Settings**:

- **Model** — pick and download chat models; follow Vokter's hardware-based recommendation.
- **Language & voice** — choose your language and voice pack.
- **Persona** — name, tone (formal / neutral / friendly), and mode.
- **Advanced** — point Vokter at your own Ollama instance via an Engine URL (optional; a local engine is bundled by default).

Your encryption key is created on first run and stored in your OS keyring — you never have to handle it.

## Roadmap

- [x] **Phase 1 — Your agent on your machine**: local LLM, AES-256 encrypted memory, RAG chat, email connector.
- [x] **Phase 2 — Your agent goes out into the world**: 100% local voice (Whisper STT + local TTS), web browsing with allow-list permissions, multi-step task planner with streaming, identity layer.
- [x] **Phase 4 — Your agent works while you sleep**: scheduled background tasks with configurable intervals, run history, autonomous execution via the planner pipeline.
- [x] **Phase 5 — Make it yours**: desktop app (`.deb`) with in-app onboarding, model management, and agent personalisation (name, tone, language, model). SQLCipher encryption with the key held in your OS keyring.
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

- Database encrypted at rest with AES-256 (SQLCipher). The key is generated on first run and stored in your OS keyring — it never leaves the machine.
- Ships as a sandboxed desktop app (AppArmor profile on Linux, plus the Chromium sandbox).
- Web browsing is allow-list only — Vokter cannot visit a domain you haven't explicitly permitted, and redirects to private/internal addresses are blocked.
- No data is ever sent to a third-party service unless you configure an external connector (e.g. an IMAP mailbox you own).

## License

AGPL-3.0 — free forever, and improvements flow back to the community.

## Community

- Discussions: the Discussions tab of this repository
- [Contribute](CONTRIBUTING.md)

---

*Vokter is an independent European project. Not affiliated with any big tech, and that's exactly the point.*
