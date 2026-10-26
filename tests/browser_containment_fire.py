#!/usr/bin/env python3
"""Tier-3 Phase 1 — interactive-browser CONTAINMENT fire test (dev wrapper).

The actual logic lives in app/containment_selftest.py (frozen into the backend, so a freshly-
installed .deb proves itself with `vokter-backend --containment-selftest`). This wrapper runs the
SAME core in a dev checkout, so a weakening of the shipped cage fails here too.

Run:  desktop/runtime/venv/bin/python tests/browser_containment_fire.py
(or point VOKTER_BROWSER_SHELL / VOKTER_PASTA at bundled binaries to exercise the packaged cage.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import containment_selftest

if __name__ == "__main__":
    raise SystemExit(containment_selftest.run())
