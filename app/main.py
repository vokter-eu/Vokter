"""
Vokter v0.1 — Tu guardián de IA local.

Backend mínimo y honesto:
- Ingesta de documentos (PDF/TXT/MD) -> troceado -> embeddings locales (Ollama)
- Almacenamiento en SQLite en TU disco
- Preguntas respondidas SOLO con tus documentos (RAG) por un LLM local
- Borrado real: eliminar un documento purga también sus embeddings

Ni una sola llamada sale de tu máquina. Compruébalo: el único host
al que habla este código es el contenedor local de Ollama.
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

# --- Configuración (todo apunta a local) ---------------------------------
OLLAMA_URL = os.getenv("VOKTER_OLLAMA_URL", "http://ollama:11434")
CHAT_MODEL = os.getenv("VOKTER_CHAT_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("VOKTER_EMBED_MODEL", "nomic-embed-text")
DB_PATH = os.getenv("VOKTER_DB", "/data/vokter.db")
CHUNK_SIZE = 900      # caracteres por trozo
CHUNK_OVERLAP = 150   # solape entre trozos
TOP_K = 4             # trozos de contexto por pregunta

app = FastAPI(title="Vokter", version="0.1.0")


# --- Base de datos --------------------------------------------------------
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


# --- Utilidades -----------------------------------------------------------
def chunk_text(text: str) -> list[str]:
    """Trocea el texto con solape para no cortar ideas por la mitad."""
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end - CHUNK_OVERLAP
    return chunks


async def embed(text: str) -> list[float]:
    """Calcula el embedding en el Ollama LOCAL."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (embeddings) respondió {r.status_code}. "
                                 f"¿Has hecho 'ollama pull {EMBED_MODEL}'?")
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
    """Ingesta un documento: extrae texto, trocea y guarda embeddings locales."""
    raw = await file.read()
    text = extract_text(file.filename, raw)
    if not text.strip():
        raise HTTPException(400, "No se pudo extraer texto de ese archivo.")
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
    """Panel 'Qué sabe Vokter': inventario completo de lo ingerido."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT doc, COUNT(*) FROM chunks GROUP BY doc ORDER BY doc"
        ).fetchall()
    return [{"doc": d, "chunks": c} for d, c in rows]


@app.delete("/api/docs/{doc_name}")
def delete_doc(doc_name: str):
    """Borrado real: el documento y TODOS sus embeddings desaparecen."""
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM chunks WHERE doc = ?", (doc_name,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Vokter no conocía ese documento.")
    return {"deleted": doc_name, "chunks_removed": cur.rowcount}


@app.post("/api/ask")
async def ask(q: Question):
    """Responde usando SOLO tus documentos como fuente."""
    with closing(get_db()) as db:
        rows = db.execute("SELECT doc, content, embedding FROM chunks").fetchall()
    if not rows:
        return {"answer": "Aún no me has enseñado ningún documento. "
                          "Sube uno y pregúntame sobre él.", "sources": []}

    q_vec = await embed(q.question)
    scored = sorted(
        ((cosine(q_vec, json.loads(emb)), doc, content) for doc, content, emb in rows),
        key=lambda t: t[0],
        reverse=True,
    )[:TOP_K]

    context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in scored)
    system = (
        "Eres Vokter, el guardián de IA personal del usuario. "
        "Responde en el idioma de la pregunta, usando ÚNICAMENTE el contexto "
        "proporcionado de sus documentos. Si la respuesta no está en el "
        "contexto, dilo honestamente: nunca inventes."
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
                     "content": f"Contexto:\n{context}\n\nPregunta: {q.question}"},
                ],
            },
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (chat) respondió {r.status_code}. "
                                 f"¿Has hecho 'ollama pull {CHAT_MODEL}'?")
    answer = r.json()["message"]["content"]
    return {"answer": answer, "sources": sorted({doc for _, doc, _ in scored})}


# --- Interfaz -------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
