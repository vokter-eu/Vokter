# Image understanding for Vokter — plan (Tier-1 OCR now; VLM deferred)

**Status:** Phase 1 (OCR text-extraction) **shipped and proven on the frozen `.deb`** (see the
frozen-build proof below). Phase 2 (VLM image Q&A) **implemented, in dev-preview** (see the Phase-2
notes at the bottom): a capability-gated VLM (`qwen2.5vl:7b`) for true image Q&A, offered only on
capable hardware, consent-pulled on demand, with the i3 floor degrading honestly to Phase-1 OCR.
Near-term, local-only, must fit the i3 floor + the bundled-Ollama / frozen `.deb` stack. This doc is
the plan + the verified research behind it.

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

## Phase-1 frozen-build proof (2026-10-10) — packaged `.deb`, not dev-disk

The dev-preview above ran the source tree; this proves the capability on the **packaged artifact**,
the failure class a dev-preview can't catch (cf. v0.12.0, where the Kokoro freeze spec silently
dropped data files and broke only in the `.deb`).

- **extraResources — tesseract is physically in the `.deb`.** `dpkg-deb -c vokter-desktop_0.18.0_amd64.deb`
  shows `./opt/Vokter/resources/desktop/runtime/tesseract/…`: the `tesseract` binary, `libtesseract.so.5`,
  `liblept.so.5`, and `cat`/`spa`/`eng` traineddata.
- **Orchestrator path resolution holds in the package.** Frozen `HERE = Path(sys.executable).parents[3]`
  → `resources/desktop`, so `TESSERACT_BIN = resources/desktop/runtime/tesseract/usr/bin/tesseract`,
  which resolves to a real file in the unpacked bundle (exactly where extraResources placed it).
- **End-to-end on the frozen binary via live `/api/docs`.** Ran the packaged
  `resources/desktop/freeze/dist/vokter-backend/vokter-backend` (SQLCipher DB, env pointed at the
  **packaged** tesseract tree), POSTed a rendered Catalan+ES receipt → `200 {"chunks":1}` (full
  pipeline: OCR → chunk → bge-m3 embed → encrypted store). Reading the stored chunk back decrypted
  shows correct diacritics: **Plaça / pagès / inclòs / Gràcies / Cafè / Atención**.
- **Size.** `vokter-desktop_0.18.0_amd64.deb` = **211.9 MB** vs v0.17.0 (no OCR) 200.2 MB → **+11.7 MB**.
  That is the vendored tesseract tree (~15 MB uncompressed) **plus Pillow + its native `pillow.libs`**
  in the frozen backend; the Phase-1 commit message's "+5.4 MB" counted only the tesseract tree and
  omitted Pillow. Prune held (no regression; tesseract did not double).

## Phase-2 implementation notes (image understanding — dev-preview)

Phase 2 adds a VLM for **image Q&A** ("what's in this photo", read a diagram/chart, messy layouts
Tesseract can't parse). Routing is by **intent/endpoint**, so Phase 1 is untouched:

- **`/api/docs` → Tesseract OCR** (all machines, unchanged): "add a document/image, reads the text".
- **`/api/ask` with an attached image → the VLM** (`Question.image`, base64): true image understanding,
  capable hardware only. No engine change — Ollama's `/api/chat` carries per-message `images`.

**Model + capability gate (`app/hwdetect.py`).** A `kind:"vision"` CATALOG entry (`qwen2.5vl:7b`,
~6 GB, strong ES/CA; alternatives `qwen3-vl:8b` / `minicpm-v:8b`). `vision_model(hw)` returns it only
when `_can_run_vision(hw)` (= the balanced bar: any GPU/Apple, or a strong CPU ≥16 GB/≥8 c); the i3
floor gets `None`. `kind:"vision"` fences it out of every CHAT consumer — `recommend()`,
`visible_catalog()`, and the orchestrator first-run pull — so it is **never** auto-picked or
auto-pulled as the chat model. `VOKTER_VISION_MODEL` overrides the id + forces availability
(power-user / dev-preview on CPU).

**Routing (`app/chat.py`).** `/api/ask` branches early on `image`: builds a user turn carrying the
image, routes to the vision model, keeps the system prompt + P2 personal-memory gate, and skips
document RAG (we're asking about *this* image). Fall-closed: if the box isn't capable, or the VLM
isn't pulled, it **degrades to Phase-1 OCR** with an honest, language-correct note (distinguishing
"needs a more capable machine" from "model not downloaded yet"), never calling a missing model.

**UX (`/api/hardware` + `app/static`).** `/api/hardware` exposes `vision {capable, model, size_gb,
installed}`. The composer shows an "ask about an image" (camera) button only when capable; if the
VLM isn't installed, the first click offers a **size-disclosed, consent-gated one-time download**
(reusing the `/api/models/pull` SSE progress flow) — a capable box is never auto-pulled. EN/ES.

**Not a chat default → no N=5 chat harness.** The VLM is opt-in (only when an image is attached), so
the chat-model confab/language harness is a category error; the right check is image-specific
(describes the image without hallucinating; EN→EN / ES→ES). Deferred, like Phase 1: the clean-VM
`.deb` fire test and a frozen-build packaged gate before release.

**Known deferred risk:** text inside an uploaded image reaches a VLM whose prompt carries personal
memory for the human session — the same indirect-prompt-injection class as Phase-1 OCR→RAG (see
docs/threat-model-prompt-injection.md), not new to Phase 2.
