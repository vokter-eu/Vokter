# -*- mode: python ; coding: utf-8 -*-
# Freeze the REAL Vokter backend (main:app via uvicorn) into a self-contained
# onedir bundle. Derived from the phase2_spike probe.spec that passed ALL_OK.
import os

from PyInstaller.utils.hooks import collect_all

APP_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "..", "app"))
# desktop/ — home of the orchestrator + key-source + keychain modules that the
# --orchestrate mode (3.3-A) runs from inside the bundle. No name collision with
# app/ (verified), so both can share pathex safely.
DESKTOP_DIR = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(APP_DIR, "static"), "static")]
binaries = []
# The orchestrate-mode modules are imported lazily (inside a branch), so name
# them explicitly to guarantee they travel in the bundle.
hiddenimports = ["orchestrator", "keysource", "keychain"]

# collect_all (never collect_data_files) for every package with native pieces:
# piper's espeakbridge.so + espeak-ng-data must travel together (spike lesson).
# secretstorage/jeepney: the OS keychain, so --orchestrate reads it in-process.
for pkg in (
    "piper",
    "sqlcipher3",
    "ctranslate2",
    "faster_whisper",
    "onnxruntime",
    "av",
    "nostr_sdk",
    "secretstorage",
    "jeepney",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["vokter_backend.py"],
    pathex=[APP_DIR, DESKTOP_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vokter-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vokter-backend",
)
