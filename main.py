"""
Vokter v0.1 — Your local AI guardian.

Minimal, honest backend:
- Document ingestion (PDF/TXT/MD) -> chunking -> local embeddings (Ollama)
- Storage in SQLite on YOUR disk
- Questions answered ONLY from your documents (RAG) by a local LLM
- Real deletion: removing a document also purges its embeddings

Not a single call leaves your machine. Check it: the only host
this code talks to is the local Ollama container.
"""

import json
import math
import os
import sqlite3
from contextlib import closing

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

# --- Configuration (everything points to local) --------------------------
OLLAMA_URL = os.getenv("VOKTER_OLLAMA_URL", "http://ollama:11434")
CHAT_MODEL = os.getenv("VOKTER_CHAT_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("VOKTER_EMBED_MODEL", "nomic-embed-text")
DB_PATH = os.getenv("VOKTER_DB", "/data/vokter.db")
CHUNK_SIZE = 900      # characters per chunk
CHUNK_OVERLAP = 150   # overlap between chunks
TOP_K = 4             # context chunks per question

app = FastAPI(title="Vokter", version="0.1.0")


# --- Database -------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               doc TEXT NOT NULL,
               content TEXT NOT NULL,
               embedding TEXT NOT NULL
           )"""
    )
    return conn


# --- Utilities ------------------------------------------------------------
def chunk_text(text: str) -> list[str]:
    """Chunk the text with overlap so ideas aren't cut in half."""
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end - CHUNK_OVERLAP
    return chunks


async def embed(text: str) -> list[float]:
    """Compute the embedding on the LOCAL Ollama."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (embeddings) returned {r.status_code}. "
                                 f"Did you run 'ollama pull {EMBED_MODEL}'?")
    return r.json()["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def extract_text(filename: str, raw: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io_bytes := __import__("io").BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    # txt, md y similares
    return raw.decode("utf-8", errors="replace")


# --- API ------------------------------------------------------------------
class Question(BaseModel):
    question: str


@app.post("/api/docs")
async def upload_doc(file: UploadFile = File(...)):
    """Ingest a document: extract text, chunk, and store local embeddings."""
    raw = await file.read()
    text = extract_text(file.filename, raw)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from that file.")
    chunks = chunk_text(text)
    with closing(get_db()) as db:
        for piece in chunks:
            vector = await embed(piece)
            db.execute(
                "INSERT INTO chunks (doc, content, embedding) VALUES (?, ?, ?)",
                (file.filename, piece, json.dumps(vector)),
            )
        db.commit()
    return {"doc": file.filename, "chunks": len(chunks)}


@app.get("/api/docs")
def list_docs():
    """'What Vokter knows' panel: full inventory of what's been ingested."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT doc, COUNT(*) FROM chunks GROUP BY doc ORDER BY doc"
        ).fetchall()
    return [{"doc": d, "chunks": c} for d, c in rows]


@app.delete("/api/docs/{doc_name}")
def delete_doc(doc_name: str):
    """Real deletion: the document and ALL its embeddings disappear."""
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM chunks WHERE doc = ?", (doc_name,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Vokter didn't know that document.")
    return {"deleted": doc_name, "chunks_removed": cur.rowcount}


@app.post("/api/ask")
async def ask(q: Question):
    """Answer using ONLY your documents as the source."""
    with closing(get_db()) as db:
        rows = db.execute("SELECT doc, content, embedding FROM chunks").fetchall()
    if not rows:
        return {"answer": "You haven't taught me any documents yet. "
                          "Upload one and ask me about it.", "sources": []}

    q_vec = await embed(q.question)
    scored = sorted(
        ((cosine(q_vec, json.loads(emb)), doc, content) for doc, content, emb in rows),
        key=lambda t: t[0],
        reverse=True,
    )[:TOP_K]

    context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in scored)
    system = (
        "You are Vokter, the user's personal AI guardian. "
        "Answer in the language of the question, using ONLY the provided "
        "context from their documents. If the answer is not in the "
        "context, say so honestly: never make things up."
    )
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": CHAT_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",
                     "content": f"Context:\n{context}\n\nQuestion: {q.question}"},
                ],
            },
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (chat) returned {r.status_code}. "
                                 f"Did you run 'ollama pull {CHAT_MODEL}'?")
    answer = r.json()["message"]["content"]
    return {"answer": answer, "sources": sorted({doc for _, doc, _ in scored})}


# --- Interface ------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
