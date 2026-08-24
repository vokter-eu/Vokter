#!/usr/bin/env python3
"""Post-freeze prune of data the voice engines *can* reach but our languages never use.

Runs AFTER pyinstaller, BEFORE electron-builder packaging. Idempotent and per-item
(flags let a failed gate drop just one item and keep the rest). Only touches:
  1a espeak  — delete *_dict for languages we don't synthesize, in BOTH espeak-ng-data
               copies (espeakng_loader's for Kokoro, piper's for Piper). Keeps every
               shared file (lang/, voices/, phontab, phonindex, phondata, intonations).
  1b hf_xet  — delete the hf_xet package (we load whisper from a local dir; HuggingFace
               is never contacted at runtime, so its Xet transfer backend is dead weight).
  1c babel   — delete babel/locale-data/*.dat except root + our languages.

Usage: prune_frozen.py <internal_dir> [--no-espeak] [--no-hf-xet] [--no-babel]
"""
import glob
import os
import shutil
import sys

KEEP_LANGS = {"en", "es", "fr", "it", "pt", "ca", "de", "nl"}


def _freed(paths):
    return sum(os.path.getsize(p) for p in paths if os.path.isfile(p))


def prune_espeak(internal):
    total = 0
    for base in (os.path.join(internal, "espeakng_loader", "espeak-ng-data"),
                 os.path.join(internal, "piper", "espeak-ng-data")):
        if not os.path.isdir(base):
            continue
        victims = [d for d in glob.glob(os.path.join(base, "*_dict"))
                   if os.path.basename(d)[:-5] not in KEEP_LANGS]
        total += _freed(victims)
        for v in victims:
            os.remove(v)
        print(f"  espeak: removed {len(victims)} unused dicts in {os.path.relpath(base, internal)}")
    return total


def prune_hf_xet(internal):
    total = 0
    for pat in ("hf_xet", "hf_xet-*", "hf_xet.libs"):
        for p in glob.glob(os.path.join(internal, pat)):
            if os.path.isdir(p):
                total += sum(os.path.getsize(os.path.join(r, f))
                             for r, _, fs in os.walk(p) for f in fs)
                shutil.rmtree(p)
            elif os.path.isfile(p):
                total += os.path.getsize(p)
                os.remove(p)
            print(f"  hf_xet: removed {os.path.relpath(p, internal)}")
    return total


def prune_babel(internal):
    ld = os.path.join(internal, "babel", "locale-data")
    if not os.path.isdir(ld):
        return 0
    victims = [f for f in glob.glob(os.path.join(ld, "*.dat"))
               if os.path.basename(f) != "root.dat"
               and os.path.basename(f).split(".")[0].split("_")[0] not in KEEP_LANGS]
    total = _freed(victims)
    for v in victims:
        os.remove(v)
    print(f"  babel: removed {len(victims)} unused locale .dat files")
    return total


def main():
    internal = sys.argv[1]
    if not os.path.isdir(internal):
        sys.exit(f"not a dir: {internal}")
    flags = set(sys.argv[2:])
    freed = 0
    if "--no-espeak" not in flags:
        freed += prune_espeak(internal)
    if "--no-hf-xet" not in flags:
        freed += prune_hf_xet(internal)
    if "--no-babel" not in flags:
        freed += prune_babel(internal)
    print(f"pruned {freed / 1024 / 1024:.1f} MB from {internal}")


if __name__ == "__main__":
    main()
