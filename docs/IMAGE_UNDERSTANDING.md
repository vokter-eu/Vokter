# Image understanding for Vokter — plan (Tier-1 OCR now; VLM deferred)

**Status:** Phase 1 (OCR text-extraction) in dev-preview; Phase 2 (VLM image Q&A) deferred to a
capable-tier upgrade. Near-term, local-only, must fit the i3 floor + the bundled-Ollama / frozen
`.deb` stack. This doc is the plan + the verified research behind it.

## The two sub-capabilities (different problems)

| | (a) Image Q&A | (b) OCR — "read the text" (receipts/tickets/labels/docs) |
|---|---|---|
| Needs | a **VLM** (vision-language model) | a **dedicated OCR engine** (or a VLM) |
| Best on local CPU | VLM — unavoidable, heavy, slow on CPU | **OCR engine** — far lighter/faster on CPU |

~80% of the ask ("read tickets/labels/receipts/documents") is **text extraction**, which a CPU OCR
engine does better/lighter/faster than any VLM on an i3. True "analyze the photo" (semantics) needs a
VLM. **Phase 1 = OCR everywhere; Phase 2 = VLM on capable hardware.**

## Verified local VLMs (Oct 2026) — for Phase 2 reference only

Checked against ollama.com pages + llama.cpp/HF (not SEO blogs):

| Model (Ollama id) | Smallest / download | i3 (CPU) usable? | ES/CA OCR | Notes |
|---|---|---|---|---|
| `moondream:1.8b` | 1.8B / 1.7 GB | yes but slow | weak | edge-tuned; only one that fits the floor |
| `qwen2.5vl:3b` | 3B / 3.2 GB (+7b/32b/72b) | 3B borderline | strong | best small all-rounder |
| `qwen3-vl` | 2B / 8B / 32B+ (GGUF since 30 Oct 2025) | 2B maybe; 8B capable-tier | strongest | the capable-tier pick |
| `minicpm-v:8b` | 8B / 5.5 GB | no (8B) | strong (SOTA OCRBench) | capable-tier only |
| `llama3.2-vision` | 11B | **no** | — | **rejected**: > floor (and reported broken on Ollama) |

**CPU reality:** VLM image inference preprocesses+encodes the image on CPU before generating →
seconds to tens of seconds/image on an i3. "Works," not "snappy." → VLM is a capable-tier upgrade.

## OCR engines (CPU) — the Phase-1 choice

| Engine | CPU speed | Footprint | ES / Catalan | Frozen-stack fit |
|---|---|---|---|---|
| **Tesseract** (LSTM) | ~0.2–1 s/page | ~tens of MB (binary + per-lang traineddata) | `spa` + **`cat`** traineddata (native diacritics) | native binary + traineddata must be bundled into the freeze |
| **RapidOCR** (PP-OCR ONNX) | ~1–2 s/image | **models 16 MB, but +153 MB opencv dep** | default (Chinese) rec model **strips diacritics**; needs a **Latin** rec model | pure wheels; **onnxruntime already bundled** |
| PaddleOCR | slower on CPU | heavy (PaddlePaddle) | 100+ langs | avoid for a frozen `.deb` |

## Stack fit & hardware-tiering

- **Decouple OCR from the VLM.** OCR = a CPU dependency baked into the frozen bundle (**not** via
  Ollama) → runs on the i3 floor with no GB download. VLM = a future `CATALOG` entry in
  `app/hwdetect.py` pulled by bundled Ollama, gated by a capability check mirroring `_capable_for_8B`.
- **Intake already exists** (the real win): `POST /api/docs` → `ingestion.extract_text(filename, raw)`
  → `chunk_text` → `rag.embed` → `chunks`; the front-end already has a file picker. Phase 1 = add an
  **image branch** to `extract_text()` + widen the `accept=` filter. Everything downstream
  (chunk / embed / RAG / cite / memory / forget) is unchanged.

## Phased plan

- **Phase 1 — OCR everywhere (Tier-1, incl. i3 floor).** New `app/ocr.py` (lazy engine) + image branch
  in `extract_text()` + widened front-end `accept=` (+ paste/drag if easy). Honest UI: *"reads text
  from images,"* not *"understands your photo."* EN/ES. Validate Catalan on a real ticket.
- **Phase 2 — VLM as a capable-tier upgrade (opt-in, deferred).** Vision `CATALOG` entry via Ollama,
  capability-gated; enables true image Q&A. On i3: optionally `moondream` on-demand with a slowness note.
- **Phase 3 — VLM-assisted OCR fallback (capable only, optional).**

## Phase-1 dev-preview findings (2026-10-04)

Built behind `app/ocr.py` (lazy) wired into `extract_text()`; proved end-to-end on a rendered ES +
Catalan receipt via the real `/api/docs` pipeline path.

- **Works:** structure + all amounts read correctly; warm ~1.7 s/image on the dev CPU; onnxruntime was
  already bundled (Kokoro/whisper), so OCR reuses it.
- **Catalan weakness (the flagged risk, confirmed):** RapidOCR's **default rec model is Chinese
  PP-OCRv4**, whose dictionary lacks accented Latin letters → it **strips diacritics**
  (`Plaça→Placa`, `pagès→pages`, `inclòs→inclos`, `Gràcies→Gracies`). Fixing it needs a **Latin**
  PP-OCR rec model sourced + bundled (RapidOCR ships only the Chinese model).
- **Size:** RapidOCR adds **~56 MB compressed** to the `.deb` (200 MB → ~256 MB) — the OCR *models* are
  only 16 MB; **~153 MB (uncompressed) is the opencv dependency.** Over the "tens of MB" target.
- **Decision (recommended): switch Phase 1 to Tesseract `spa`+`cat`.** Both stated constraints favour
  it — smaller (~tens of MB) and **native diacritics** via the purpose-built `cat` model — and the
  pre-agreed rule was "if RapidOCR's Catalan is weak, switch to Tesseract." Cost: bundling the native
  `tesseract` binary + traineddata into the PyInstaller freeze (the known packaging trade-off). The
  `app/ocr.py` abstraction keeps the swap localized. *(Alternative kept on file: RapidOCR + a bundled
  Latin rec model — keeps pure-wheel packaging and fixes diacritics, but stays +56 MB from opencv.)*
