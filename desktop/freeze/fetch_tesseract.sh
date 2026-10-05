#!/usr/bin/env bash
# Vendor Tesseract for the frozen bundle — Tier-1 image OCR (see docs/IMAGE_UNDERSTANDING.md).
#
# Downloads the Debian packages for the tesseract binary + libtesseract/leptonica + the
# cat/spa/eng traineddata and extracts them into desktop/runtime/tesseract/, where
# electron-builder's extraResources picks them up. No sudo, no system install (apt-get
# download + dpkg-deb -x). ~15 MB uncompressed / ~5.4 MB inside the .deb.
#
# Re-run to refresh. The orchestrator points the backend at this tree via
# VOKTER_TESSERACT_CMD / VOKTER_TESSDATA_PREFIX / VOKTER_TESSERACT_LIBDIR.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/../runtime/tesseract"
PKGS="tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-cat libtesseract5 liblept5"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Fetching: $PKGS"
( cd "$TMP" && apt-get download $PKGS )

rm -rf "$DEST"; mkdir -p "$DEST"
for d in "$TMP"/*.deb; do dpkg-deb -x "$d" "$DEST"; done

echo "Vendored Tesseract → $DEST ($(du -sh "$DEST" | cut -f1))"
LD_LIBRARY_PATH="$DEST/usr/lib/x86_64-linux-gnu" \
  "$DEST/usr/bin/tesseract" --version >/dev/null 2>&1 \
  && echo "smoke-test OK" || { echo "WARN: tesseract binary smoke-test failed"; exit 1; }
