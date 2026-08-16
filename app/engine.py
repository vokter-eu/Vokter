"""
Engine adapter — the seam that keeps Vokter neutral about which inference
engine runs the model, and about WHERE it runs.

The rest of Vokter speaks only this interface (chat / embed). It never learns
the engine's URL, whether the engine is local or remote, what a request costs,
or whether the engine had to prove itself before answering — those are all the
adapter's private business. Adding a new engine (llama.cpp, LocalAI, a remote
confidential-compute node…) means writing one new InferenceEngine; nothing
else in Vokter changes.

Design constraints (see the project-vokter-phase7-confidential memory), so a
future "remote confidential compute" adapter slots in without rework:
  * no "where it runs" in the contract — host/network are the adapter's detail
  * calls may be slow and may fail — async, per-call timeout, HTTPException
  * cost is NOT in the signatures — a paid remote adapter settles internally
  * attestation is NOT in the signatures — trust setup is the adapter's own
  * model provisioning is NOT part of this interface — an adapter that manages
    nothing locally (a remote one) simply has no such step

This module only defines the neutral contract and the default Ollama adapter.
The Ollama adapter talks Ollama's native /api/* exactly as Vokter did before
the seam existed — same payloads, same num_ctx, same json mode, byte for byte.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import HTTPException

from config import OLLAMA_URL, CHAT_MODEL, EMBED_MODEL


@dataclass
class ChatRequest:
    """A chat request in Vokter's own vocabulary — not any vendor's API shape.

    ``timeout`` is the caller's patience, not an assumption about the engine:
    a remote adapter may internally wait longer, but how long *this* caller is
    willing to block stays the caller's decision.
    """
    messages: list[dict]
    model: str | None = None          # None → the adapter's default chat model
    context_size: int | None = None   # context window in tokens; None → engine default
    json_mode: bool = False           # force a strict-JSON reply
    temperature: float | None = None  # None → engine default (unset); 0 for deterministic
    timeout: float = 300.0


class InferenceEngine(Protocol):
    """What every engine must provide. Deliberately just two operations —
    everything about location, cost, trust and model provisioning is hidden
    inside the implementation, never surfaced here."""

    async def chat(self, req: ChatRequest) -> str: ...

    async def embed(self, text: str, model: str | None = None,
                    timeout: float = 120.0) -> list[float]: ...


class OllamaEngine:
    """Default adapter: talks to Ollama's native /api/* endpoints.

    This is a faithful move of the request-building and response-parsing that
    used to live inline in chat.py / rag.py / planner.py — same wire payloads,
    same error messages (it owns the "Ollama" name because it *is* Ollama)."""

    def __init__(self, base_url: str = OLLAMA_URL,
                 default_chat_model: str = CHAT_MODEL,
                 default_embed_model: str = EMBED_MODEL):
        self._base = base_url
        self._chat_model = default_chat_model
        self._embed_model = default_embed_model

    async def chat(self, req: ChatRequest) -> str:
        model = req.model or self._chat_model
        payload: dict = {"model": model, "stream": False, "messages": req.messages}
        # Order/keys below reproduce the old inline payloads exactly: `format`
        # only when json mode is asked for, `options.num_ctx` only when a
        # context size is given (the planner's plan step sent neither).
        if req.json_mode:
            payload["format"] = "json"
        # `options` stays absent unless a knob is set, so existing callers send
        # byte-identical payloads (temperature=None omits it entirely).
        options: dict = {}
        if req.context_size is not None:
            options["num_ctx"] = req.context_size
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if options:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=req.timeout) as client:
            r = await client.post(f"{self._base}/api/chat", json=payload)
        if r.status_code != 200:
            raise HTTPException(502, f"Ollama (chat) returned {r.status_code}. "
                                     f"Did you run 'ollama pull {model}'?")
        try:
            return r.json()["message"]["content"]
        except (json.JSONDecodeError, KeyError):
            raise HTTPException(502, "Unexpected response format from Ollama")

    async def embed(self, text: str, model: str | None = None,
                    timeout: float = 120.0) -> list[float]:
        model = model or self._embed_model
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{self._base}/api/embeddings",
                                  json={"model": model, "prompt": text})
        if r.status_code != 200:
            raise HTTPException(502, f"Ollama (embeddings) returned {r.status_code}. "
                                     f"Did you run 'ollama pull {model}'?")
        try:
            vec = r.json()["embedding"]
        except (json.JSONDecodeError, KeyError):
            raise HTTPException(502, "Ollama returned an unexpected embedding response")
        if not vec:
            raise HTTPException(502, "Ollama returned an empty embedding — is the model loaded?")
        return vec


def resolve_base_url() -> str:
    """The engine base URL for THIS request, honouring the user's engine_url setting.

    Empty (the default) → the bundled sovereign engine (config.OLLAMA_URL): app-local,
    no-cloud. A configured http(s) URL → the user's OWN Ollama, their opt-in step
    OUTSIDE Vokter's no-cloud control. Read per call, never cached in a module global,
    because the setting can change at runtime via /api/config — a stale global would
    keep talking to the old engine after the user switched. Trailing slash trimmed so
    it composes cleanly with the adapter's f"{base}/api/…" (a value saved outside the
    /api/config validator can't sneak a double slash through)."""
    from agent_config import get_config          # local import keeps engine.py free of a
                                                 # startup dependency on the DB layer
    return ((get_config().get("engine_url") or "").strip().rstrip("/")) or OLLAMA_URL


def get_engine() -> InferenceEngine:
    """The single place Vokter chooses an engine. Today it is Ollama, with no question
    asked of the user (neutral inside, simple outside). Built fresh per call — cheap,
    it only stashes a few strings; the HTTP client is created per request inside
    chat()/embed() as before — so the user's engine_url override takes effect the
    instant they save it. Every call site goes through here, so a second engine (a
    remote confidential-compute node…) means growing this factory, nothing else."""
    return OllamaEngine(base_url=resolve_base_url())
