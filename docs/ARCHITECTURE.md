# Vokter — Architecture

This document defines the technical architecture across all phases. Every feature we build must be consistent with what's written here. When in doubt, this document wins.

---

## Core principle

Vokter is a local-first sovereign agent. The user's machine is the trust boundary. Nothing crosses that boundary without explicit user permission. This is not a UX choice — it is the architectural invariant that everything else derives from.

---

## Phase map

| Phase | Name | What it adds |
|-------|------|-------------|
| 0 | Skeleton | Repo, manifesto, Docker scaffold |
| 1 | Local guardian | Document ingestion, local RAG, conversation memory, encrypted SQLite |
| 2 | Agent goes out | Web browsing, task planning, local voice (Whisper + Piper), identity layer |
| 4 | Agent works while you sleep | Scheduled recurring tasks, run history, autonomous planner pipeline |
| 5 | Make it yours | Agent personalisation, persistent conversation history, Docker-first setup |
| 6 | Agent talks to other agents | MCP server adapter, Nostr DM adapter |

---

## Identity architecture

This is the most critical design decision in the project. Get it wrong here and every phase that follows is compromised.

### The problem

A persistent public key (e.g. a Nostr npub) is a **correlation vector**. If Vokter uses the same identity across external services, those services — individually or by sharing data — can reconstruct a detailed picture of the user's life: where they travel, what they buy, who they talk to. This defeats the privacy mission entirely.

### The solution: three-layer identity

```
┌─────────────────────────────────────────────────┐
│  Layer 1 — Master Key                           │
│  Never leaves the local machine.                │
│  Stored in encrypted SQLite (VOKTER_DB_KEY).    │
│  Used only to derive session keys.              │
└────────────────────┬────────────────────────────┘
                     │ derives (unlinkable)
                     ▼
┌─────────────────────────────────────────────────┐
│  Layer 2 — Ephemeral Session Keys               │
│  One fresh keypair per external interaction.    │
│  Airline sees key A. Hotel sees key B.          │
│  Theatre sees key C.                            │
│  A, B, C are mathematically unlinkable.         │
│  Derived from: HMAC(master_secret, nonce)       │
│  Nonces are random and stored locally.          │
└─────────────────────────────────────────────────┘
```

**Rule**: the master key is never sent to any external service, ever. External services only ever see ephemeral session keys that are rotated per interaction.

### Phase 6: Nostr identity

Vokter's Nostr keypair is derived from the master key (secp256k1). This gives Vokter a stable, self-sovereign identity for agent-to-agent communication without any registration, account, or persistent identifier in a third-party system. The key never leaves the machine.

---

## Interoperability architecture (Phase 6)

The tool registry is the source of truth. Protocol adapters sit above it and translate, never contain business logic.

```
┌──────────────────────────────────────────────────┐
│                 Tool Registry                    │
│  browse │ ask │ plan │ schedule                  │
└──────────────────┬───────────────────────────────┘
                   │
        ┌──────────┼──────────┐
        │          │          │
       REST        MCP      Nostr
   (Phase 1–5)  (Phase 6)  (Phase 6)
   /api/* done   adapter    adapter
```

- **REST** — already exists (`/api/*`). No changes needed.
- **MCP** — a new `app/mcp_server.py` wraps the same tools as an MCP server. Libraries: `mcp`. No changes to core.
- **Nostr** — a new `app/nostr_listener.py`. Encrypted DMs arrive → tool registry call → encrypted reply to sender. Keypair derived from master key (Layer 1).

**Invariant**: no adapter contains business logic. Each adapter is a protocol translation layer only.

---

## Module structure

```
app/
  main.py             — FastAPI app init and route registration
  config.py           — All env vars and constants
  db.py               — SQLCipher connection, schema (CREATE TABLE IF NOT EXISTS)
  ingestion.py        — Document parsing, chunking, embedding storage
  rag.py              — Retrieval and context assembly
  chat.py             — Conversation history (SQLite) + Ollama interaction
  agent_config.py     — Agent personalisation settings (name, tone, mode, language, models)
  config_routes.py    — GET/PATCH /api/config endpoints
  identity.py         — Master key generation and ephemeral session key derivation
  browser.py          — Web fetching, allowlist enforcement, content extraction
  planner.py          — Multi-step task planner, SSE streaming
  scheduler.py        — Recurring task runner (asyncio background loop)
  schedule_routes.py  — Scheduled task CRUD endpoints
  email_connector.py  — IMAP/SSL email sync
  utils.py            — Shared helpers
  voice/
    __init__.py
    whisper.py        — Speech-to-text (faster-whisper, CPU)
    piper.py          — Text-to-speech (Piper)
  static/
    index.html        — Single-page UI
```

---

## Local engine strategy

Inference runs locally. Vokter speaks to its own internal contract — `chat` and
`embed` in `engine.py` (the `InferenceEngine` protocol) — never to a specific
engine's API. The default `OllamaEngine` is one implementation of that contract;
swapping the **runner** means writing one more translator file, and nothing else
in Vokter changes.

**The key distinction — model vs runner:**

- **Changing the MODEL** (`llama3.2` → Qwen3 → whatever ships next) is *free*: a
  config string (`VOKTER_CHAT_MODEL`). This is ~90% of the quality gains to come,
  and costs nothing architecturally.
- **Changing the RUNNER** (Ollama → llama.cpp / MLX / other) is a *new adapter* —
  one file implementing `InferenceEngine`. It will happen far less often than a
  model swap.

**⚠️ Embeddings caveat — the one place that is NOT free.** Changing the
*embedding* model (currently `bge-m3`, 1024-dim) requires RE-INDEXING everything
embedded with the old one: RAG document chunks *and* personal memory. Different
models produce vectors in different spaces; a mismatch silently degrades retrieval
(`rag.cosine` even returns 0 on a dimension mismatch). This is now handled
automatically by the background `migrations.reembed_stale()` pass, which re-embeds
any row whose stored vector dimension doesn't match the live model (e.g. a stale
768-dim vector from the old `nomic-embed-text` after the switch to `bge-m3`); until
a row is re-embedded it degrades cleanly to keyword/FTS retrieval. The chat side is
100% engine-neutral; the embedding side is not.

**Future runner candidates:** llama.cpp (the natural second adapter — what Jan
uses), MLX (if a Mac target ever lands). Ollama remains the best choice today:
packageable, manages model download/storage, and proven end-to-end (the Linux
`.deb` ships it).

**The adapter is not "write once and forget."** If a future runner brings
something the contract doesn't cover (a different streaming shape, dynamic
quantization), the contract is *extended* to express it. What stays invariant is
that the rest of Vokter never learns which engine runs, where it runs, or what it
costs — those remain the adapter's private business.

---

## Database schema

All tables are created idempotently in `db.py` via `CREATE TABLE IF NOT EXISTS`. The database is encrypted with SQLCipher (AES-256) using `VOKTER_DB_KEY`.

| Table | Purpose |
|-------|---------|
| `chunks` | RAG document chunks with embeddings |
| `synced_emails` | Email sync state (message IDs) |
| `identity_keys` | Master key (one row, never exported) |
| `session_nonces` | Per-interaction nonces for ephemeral key derivation |
| `browse_allowlist` | User-approved domain patterns |
| `scheduled_tasks` | Recurring task definitions |
| `task_runs` | Execution history for scheduled tasks |
| `agent_config` | Key-value store for personalisation settings |
| `conversations` | Persistent conversation history (role, content, timestamp) |

---

## What Vokter will never do

These are architectural constraints, not policy choices. Violating them would require a fork.

1. Send any request to a third-party AI API (all inference is local via Ollama)
2. Use a persistent public key as an external-facing identity
3. Hold custody of user funds
4. Make any payment without explicit user confirmation
5. Use engagement mechanics, retention design, or exploit loneliness
6. Delete a document without also deleting its embeddings
