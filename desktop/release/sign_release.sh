#!/usr/bin/env bash
# Release signing — Bitcoin-Core pattern (SHA256SUMS + detached GPG signature).
#
# RUNS LOCALLY, where the release keyring lives. NEVER in CI: CI has no key, and a
# key that CI can reach is a key an attacker who owns the pipeline can sign with.
# This is a MANDATORY release-cut step (vokter-internal/RELEASE_BUILD_METHOD.md).
#
# What it does, given the artifact dir (default: desktop/electron/dist):
#   1. SHA256SUMS      — sha256 of every release artifact (*.deb, *.AppImage)
#   2. SHA256SUMS.asc  — armored DETACHED signature of SHA256SUMS, made by the
#                        pinned Vokter release key (below)
#   3. self-verify     — gpg --verify + sha256sum -c re-run before declaring done
#
# The user verifies (docs/VERIFY.md): import vokter-release-pubkey.asc → check the
# fingerprint → gpg --verify SHA256SUMS.asc SHA256SUMS → sha256sum -c SHA256SUMS.
#
# VOKTER_SIGN_KEY overrides the key (test harness only — a throwaway key proves the
# mechanism without touching the release key). A release cut MUST use the default.
set -euo pipefail

RELEASE_FPR="775D4008FB7D0EE1D369BF174F2E21E678C24E4B"   # Vokter Releases — pinned
SIGN_KEY="${VOKTER_SIGN_KEY:-$RELEASE_FPR}"

DIST="${1:-$(cd "$(dirname "$0")/../electron/dist" 2>/dev/null && pwd)}"
[ -d "$DIST" ] || { echo "ERROR: artifact dir not found: $DIST" >&2; exit 1; }
cd "$DIST"

shopt -s nullglob
ARTIFACTS=(*.deb *.AppImage)
[ ${#ARTIFACTS[@]} -gt 0 ] || { echo "ERROR: no *.deb / *.AppImage in $DIST" >&2; exit 1; }

# the signing key must be in the LOCAL keyring (secret part never leaves it)
gpg --list-secret-keys "$SIGN_KEY" >/dev/null 2>&1 \
  || { echo "ERROR: signing key $SIGN_KEY has no secret in this keyring — run on the release machine." >&2; exit 1; }

echo "[sign] artifacts: ${ARTIFACTS[*]}"
sha256sum "${ARTIFACTS[@]}" > SHA256SUMS
rm -f SHA256SUMS.asc
gpg --local-user "${SIGN_KEY}!" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS

echo "[sign] self-verify:"
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS

echo "[sign] done → $DIST/SHA256SUMS + SHA256SUMS.asc (publish BOTH next to the artifacts)"
