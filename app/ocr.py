"""
Local OCR for image intake — Tier-1 image understanding (text extraction only).

Reads text off images (receipts, tickets, labels, documents) entirely on-device via
**Tesseract** (`cat`+`spa`+`eng` by default) — chosen over RapidOCR because Tesseract
keeps Catalan/Spanish diacritics correct (its `cat` model is purpose-built) and its whole
footprint is ~15 MB (binary + libs + traineddata) vs RapidOCR's ~56 MB opencv dependency.
See docs/IMAGE_UNDERSTANDING.md.

This is OCR ("read the text in the image"), NOT image understanding ("describe the photo")
— that is the deferred capable-tier VLM upgrade. The UI must say so.

On-device, no cloud, no VLM, nothing leaves the machine. CPU-only and LAZY: the Tesseract
binary is only invoked when an image is actually uploaded, so text/PDF users and the boot
path pay nothing.

Bundling: the frozen `.deb` vendors the Tesseract binary + libs + traineddata and points
these env vars at them (system install is the dev fallback):
  VOKTER_TESSERACT_CMD      — path to the tesseract binary (else found on PATH)
  VOKTER_TESSDATA_PREFIX    — dir holding <lang>.traineddata
  VOKTER_TESSERACT_LIBDIR   — dir with libtesseract.so.5 / liblept.so.5 (prepended to LD_LIBRARY_PATH)
  VOKTER_OCR_LANGS          — override the language set (default "cat+spa+eng")
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess

# Raster image types we route to OCR.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")

_LANGS = os.getenv("VOKTER_OCR_LANGS", "cat+spa+eng")
_TIMEOUT = 60  # seconds — a generous cap for a large/slow-CPU scan

# A PDF page whose text layer carries fewer real (non-space) characters than this is treated as
# scanned (no usable text layer) → rasterise + OCR it. Pages with real text stay on the fast path.
_MIN_PAGE_CHARS = 10
# Rasterise scanned pages at ~300 DPI (PDF user space is 72 DPI) — the sweet spot for Tesseract.
_PDF_RENDER_SCALE = 300 / 72
# Cap how many scanned pages we OCR in one document (~tens of seconds each on CPU): a 466-page scan
# must not run for hours. Text-layer pages are free and never count against this. Overridable.
_MAX_OCR_PAGES = int(os.getenv("VOKTER_MAX_OCR_PAGES", "25"))


def is_image(filename: str) -> bool:
    return bool(filename) and filename.lower().endswith(IMAGE_EXTS)


def _tesseract_cmd() -> str | None:
    """Bundled binary (frozen) wins; else the one on PATH (dev / system install)."""
    cmd = os.getenv("VOKTER_TESSERACT_CMD")
    if cmd and os.path.isfile(cmd):
        return cmd
    return shutil.which("tesseract")


def _run_env() -> dict:
    """Env for the tesseract subprocess — points at the vendored tessdata/libs when set."""
    env = dict(os.environ)
    tdata = os.getenv("VOKTER_TESSDATA_PREFIX")
    if tdata:
        env["TESSDATA_PREFIX"] = tdata
    libdir = os.getenv("VOKTER_TESSERACT_LIBDIR")
    if libdir:
        prev = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = libdir + (os.pathsep + prev if prev else "")
    return env


def _to_png(raw: bytes) -> bytes | None:
    """Normalise any supported image to PNG via Pillow, so odd formats (webp/heic/…) and
    rotations still reach Tesseract as something leptonica reads cleanly."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


def _tesseract_png(png: bytes) -> str:
    """Run the vendored/system Tesseract on PNG bytes → text. "" on any failure (engine absent,
    timeout, or nothing legible). Pure w.r.t. the bytes; no network."""
    cmd = _tesseract_cmd()
    if not cmd:
        return ""  # OCR engine not present → degrade gracefully
    try:
        proc = subprocess.run(
            [cmd, "-", "-", "-l", _LANGS],  # stdin → stdout
            input=png, capture_output=True, timeout=_TIMEOUT, env=_run_env(),
        )
    except Exception:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


def ocr_image(raw: bytes) -> str:
    """Extract text from image bytes. Returns "" if OCR is unavailable or nothing legible.

    The caller treats "" as "couldn't read this image".
    """
    png = _to_png(raw)
    return _tesseract_png(png) if png is not None else ""


def _page_has_text(text: str) -> bool:
    """A page is 'already readable' once its text layer carries enough real characters."""
    return sum(1 for c in text if not c.isspace()) >= _MIN_PAGE_CHARS


def pdf_needs_ocr(page_texts: list[str]) -> bool:
    """True if any page lacks a usable text layer — i.e. a scanned/image PDF, or a mixed doc with
    some scanned pages. A fully text-layer PDF returns False and never touches the OCR path."""
    return any(not _page_has_text(t) for t in page_texts)


def ocr_pdf(raw: bytes, page_texts: list[str]) -> str:
    """OCR only the scanned pages of a PDF; pages that already have a text layer pass through
    unchanged (never rasterised). `page_texts` is pypdf's per-page extraction, in the same page
    order pdfium uses. The rasteriser (pypdfium2) is imported lazily and the document opened only
    if at least one page actually needs it; if the rasteriser is unavailable we degrade to whatever
    text layer exists rather than failing the upload. A render/OCR hiccup on one page keeps the rest.
    """
    try:
        import pypdfium2 as pdfium
    except Exception:
        return "\n".join(page_texts)
    doc = None
    out: list[str] = []
    ocr_pages = 0
    truncated = False
    try:
        for i, text in enumerate(page_texts):
            if _page_has_text(text):
                out.append(text)          # text-layer page — free, never counts against the cap
                continue
            if ocr_pages >= _MAX_OCR_PAGES:
                truncated = True          # past the cap: skip OCR but keep scanning for text pages
                continue
            if doc is None:
                doc = pdfium.PdfDocument(raw)
            try:
                pil = doc[i].render(scale=_PDF_RENDER_SCALE).to_pil()
                buf = io.BytesIO()
                pil.save(buf, "PNG")
                out.append(_tesseract_png(buf.getvalue()))
                ocr_pages += 1
            except Exception:
                out.append(text)          # best-effort: keep the (empty) text-layer for this page
    finally:
        if doc is not None:
            doc.close()
    if truncated:
        out.append(f"[Vokter: this scanned PDF is long — only the first {_MAX_OCR_PAGES} scanned "
                   f"pages were read by OCR.]")
    return "\n".join(out)
