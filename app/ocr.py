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


def ocr_image(raw: bytes) -> str:
    """Extract text from image bytes. Returns "" if OCR is unavailable or nothing legible.

    Pure w.r.t. the bytes; no network. The caller treats "" as "couldn't read this image".
    """
    cmd = _tesseract_cmd()
    if not cmd:
        return ""  # OCR engine not present → degrade gracefully
    png = _to_png(raw)
    if png is None:
        return ""
    try:
        proc = subprocess.run(
            [cmd, "-", "-", "-l", _LANGS],  # stdin → stdout
            input=png, capture_output=True, timeout=_TIMEOUT, env=_run_env(),
        )
    except Exception:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()
