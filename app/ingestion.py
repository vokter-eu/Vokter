import io
from contextlib import closing

from fastapi import APIRouter, File, HTTPException, UploadFile
from pypdf import PdfReader

from config import CHUNK_SIZE, CHUNK_OVERLAP
from db import get_db
from embedding import pack_embedding
from rag import embed

router = APIRouter()


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        piece = text[start:start + CHUNK_SIZE].strip()
        if piece:
            chunks.append(piece)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def extract_text(filename: str, raw: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8", errors="replace")


@router.post("/api/docs")
async def upload_doc(file: UploadFile = File(...)):
    raw  = await file.read()
    text = extract_text(file.filename, raw)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from that file.")
    doc_name = file.filename or "untitled"
    chunks = chunk_text(text)
    vectors = [await embed(piece) for piece in chunks]  # all embeds before touching DB
    with closing(get_db()) as db:
        db.execute("DELETE FROM chunks WHERE doc = ?", (doc_name,))
        for piece, vector in zip(chunks, vectors):
            db.execute(
                "INSERT INTO chunks (doc, content, embedding) VALUES (?, ?, ?)",
                (doc_name, piece, pack_embedding(vector)),
            )
        db.commit()
    return {"doc": doc_name, "chunks": len(chunks)}


@router.get("/api/docs")
def list_docs():
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT doc, COUNT(*) FROM chunks GROUP BY doc ORDER BY doc"
        ).fetchall()
    return [{"doc": d, "chunks": c} for d, c in rows]


@router.delete("/api/docs/{doc_name}")
def delete_doc(doc_name: str, confirm: bool = False):
    from safety import HUMAN, enforce_http
    enforce_http("doc.delete", doc_name, context=HUMAN, confirmed=confirm)
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM chunks WHERE doc = ?", (doc_name,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Vokter didn't know that document.")
    return {"deleted": doc_name, "chunks_removed": cur.rowcount}
