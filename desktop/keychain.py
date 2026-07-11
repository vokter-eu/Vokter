#!/usr/bin/env python3
"""OS keychain access for the DB encryption key — Phase 3.2, step 1 (tools only).

Not wired into the orchestrator yet. It only provides the primitives a later
phase will use to move the DB key from a plaintext file into the OS keychain.

Golden rule of Phase 3.2 (non-negotiable):

    The file key (desktop/runtime/data/.db_key) stays the PERMANENT fallback.
    Vokter must never lock a user out of their own database, and must NEVER pop
    a keychain dialog the user did not ask for.

Why this talks to GNOME Keyring DIRECTLY (via secretstorage) instead of the
python-keyring convenience API:

    The default python-keyring API routes through a *ChainerBackend* that probes
    EVERY installed backend in turn. On a GNOME box that merely has the KWallet
    package installed (a loose dependency), that probe D-Bus-activates kwalletd
    and pops a "create a new wallet" dialog — for a keychain the user does not
    even use. So we pin the real store, GNOME Keyring's Secret Service
    (org.freedesktop.secrets), and never let anything reach for KWallet.

The hardest correctness point — the reason this is a separate, testable unit —
is distinguishing two situations a naive port would confuse:

    * keychain AVAILABLE but EMPTY      → genuinely no key yet (first run)
    * keychain UNAVAILABLE (locked, no  → we simply couldn't ask
      D-Bus session, headless, hung)

Both would surface as "no value", and minting a fresh key because the keychain
"looked empty" while it was really just unreachable would open an EXISTING
database with the WRONG key and lock the user out. So availability is proven
POSITIVELY, with a self-deleting write round-trip, before any mint/migrate
decision. And a LOCKED collection is treated as unavailable WITHOUT ever calling
unlock() — unlocking is exactly what would raise a dialog.

Scope note: this module touches ONLY the keychain. It never reads or writes
.db_key or vokter.db. Those files are out of its reach by design.

(Future note: if process supervision ever moves entirely into Electron, keychain
ownership may move to Node's safeStorage. Python is the right home while the
Python orchestrator is still the booter — see orchestrator.py Phase-3 comment.)
"""
from __future__ import annotations

import secrets
import threading

import secretstorage
from secretstorage.exceptions import ItemNotFoundException

# Fixed identifiers for the REAL key slot. Changing these later = a lost key
# (the keychain would look "empty" under the new names), so they are pinned.
SERVICE = "vokter"
KEY_NAME = "db_key"

# Distinct namespace for the throwaway availability probe, so a probe item can
# never be confused with, or collide with, the real key slot.
_PROBE_SERVICE = "vokter-probe"

# The persistent (login) collection is addressed by the standard "default"
# alias; we fall back to the /login collection if the alias is unset.
_ALIAS = "default"

# Ceiling for any single keychain operation. A locked/headless keychain can
# block on D-Bus; we must never let that hang the boot, so an overrun is treated
# as "unavailable" and the caller falls back to the file key.
DEFAULT_TIMEOUT = 5.0


class _CallResult:
    """Outcome of a keychain call run under a timeout: one of ok/error/timeout."""

    __slots__ = ("status", "value", "error")

    def __init__(self, status: str, value=None, error: BaseException | None = None):
        self.status = status      # "ok" | "error" | "timeout"
        self.value = value
        self.error = error


def _call(fn, timeout: float) -> _CallResult:
    """Run fn() in a daemon thread and give up after `timeout` seconds.

    A hung keychain (locked collection waiting on a prompt, dead D-Bus) must not
    freeze the caller, so an overrun is reported as a timeout rather than
    blocking forever. The thread is a daemon: if it is still stuck when we
    return, it cannot keep the process alive.
    """
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — any failure ⇒ "not usable"
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return _CallResult("timeout")
    if "error" in box:
        return _CallResult("error", error=box["error"])  # type: ignore[arg-type]
    return _CallResult("ok", value=box.get("value"))


def _attrs(service: str, name: str) -> dict[str, str]:
    """Attribute schema used to store and look items up. The 'application' tag
    keeps our items identifiable and separate from anything else in the login
    collection."""
    return {"application": "vokter", "service": service, "username": name}


def _default_collection(conn):
    """The persistent login collection — resolved WITHOUT creating or unlocking.

    Uses the standard "default" alias; falls back to the /login collection path
    if the alias is not set. Never calls unlock(): a locked collection is caught
    by is_locked() checks in the callers, which then treat it as unavailable.
    """
    try:
        return secretstorage.get_collection_by_alias(conn, _ALIAS)
    except ItemNotFoundException:
        for c in secretstorage.get_all_collections(conn):
            if c.collection_path.endswith("/login"):
                return c
        raise


def active_backend_name() -> str:
    """Human-readable description of exactly what this module talks to."""
    return "GNOME Keyring / Secret Service (org.freedesktop.secrets), pinned"


class _Locked(RuntimeError):
    """Raised internally when the collection exists but is locked — meaning we
    could not ASK, not that the slot is empty. Never triggers an unlock/dialog."""


def is_available(timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Prove POSITIVELY that the keychain is usable, via a self-deleting probe.

    Returns True only if, within the timeout, the default collection is present
    and UNLOCKED and we can write a random probe item, read it back identical,
    and delete it. A locked collection, missing service, exception, or timeout
    ⇒ False ("treat as unavailable, fall back to the file key"). No unlock() is
    ever attempted, so this can never raise a dialog. The probe uses a distinct
    throwaway namespace, so the real key slot is never touched.
    """
    def probe() -> bool:
        conn = secretstorage.dbus_init()
        try:
            coll = _default_collection(conn)
            if coll.is_locked():
                return False  # available-but-locked = unavailable; never unlock
            probe_name = "__probe__" + secrets.token_hex(8)
            probe_value = secrets.token_hex(16)
            item = coll.create_item(
                "vokter keychain probe (ephemeral)",
                _attrs(_PROBE_SERVICE, probe_name),
                probe_value.encode("utf-8"),
            )
            try:
                got = None
                for it in coll.search_items(_attrs(_PROBE_SERVICE, probe_name)):
                    got = it.get_secret().decode("utf-8")
                    break
                return got == probe_value
            finally:
                item.delete()  # self-cleaning: leave nothing behind
        finally:
            conn.close()

    r = _call(probe, timeout)
    return r.status == "ok" and r.value is True


def get_key(
    service: str | None = None,
    name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Read a stored value, or None if absent OR if the keychain couldn't answer.

    IMPORTANT: a None here is intentionally ambiguous and MUST NOT be treated as
    "no key exists". Call is_available() first to disambiguate before making any
    mint/migrate decision. This getter deliberately stays dumb, and never
    unlocks a locked collection (that is reported as couldn't-ask → None).

    service/name default to the pinned SERVICE/KEY_NAME, resolved dynamically so
    tests can repoint them to a throwaway slot via monkeypatching.
    """
    service = service or SERVICE
    name = name or KEY_NAME

    def worker():
        conn = secretstorage.dbus_init()
        try:
            coll = _default_collection(conn)
            if coll.is_locked():
                raise _Locked("default collection is locked")
            for item in coll.search_items(_attrs(service, name)):
                return item.get_secret().decode("utf-8")
            return None
        finally:
            conn.close()

    r = _call(worker, timeout)
    return r.value if r.status == "ok" else None


def set_key(
    value: str,
    service: str | None = None,
    name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Store a value, raising if the keychain refused, was locked, or hung.

    Raising (rather than silently doing nothing) matters: a caller that thinks
    it mirrored the key into the keychain when it did not would draw wrong
    conclusions later. Let failure be loud; the caller keeps the file anyway.
    """
    service = service or SERVICE
    name = name or KEY_NAME

    def worker():
        conn = secretstorage.dbus_init()
        try:
            coll = _default_collection(conn)
            if coll.is_locked():
                raise _Locked("default collection is locked")
            coll.create_item(
                f"{service}/{name}", _attrs(service, name),
                value.encode("utf-8"), replace=True,
            )
            return True
        finally:
            conn.close()

    r = _call(worker, timeout)
    if r.status == "ok":
        return
    if r.status == "timeout":
        raise TimeoutError(f"keychain set timed out after {timeout}s for {service}/{name}")
    raise RuntimeError(f"keychain set failed for {service}/{name}: {r.error!r}")


def delete_key(
    service: str | None = None,
    name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Delete a stored value. True if something was deleted, False if absent,
    locked, or unreachable.

    Provided for test cleanup and completeness. Phase 3.2 never deletes the real
    key slot in normal operation (Fase 4 was descartada — the file stays).
    """
    service = service or SERVICE
    name = name or KEY_NAME

    def worker():
        conn = secretstorage.dbus_init()
        try:
            coll = _default_collection(conn)
            if coll.is_locked():
                return False
            deleted = False
            for item in coll.search_items(_attrs(service, name)):
                item.delete()
                deleted = True
            return deleted
        finally:
            conn.close()

    r = _call(worker, timeout)
    return r.status == "ok" and r.value is True


# --- Phase 2: the reversible mirror -----------------------------------------
# Status strings returned by mirror(), for logging and tests.
MIRROR_SKIPPED = "skipped-unavailable"   # keychain not usable → file-only, fine
MIRROR_SYNCED = "already-synced"         # keychain already held the same key
MIRROR_DONE = "mirrored"                 # wrote the file key into the keychain
MIRROR_ERROR = "error"                   # something failed; caller keeps the file


def mirror(
    key: str,
    service: str | None = None,
    name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Best-effort file→keychain mirror. NEVER raises.

    Phase 2 semantics: the FILE stays the single source of truth. This only
    makes the keychain hold a COPY of `key`, so a later phase can start reading
    from it. Nothing the caller returns or depends on changes here — if the
    keychain is absent, locked, hung, or the write fails, we simply report it
    and the caller carries on with the file key (full reversibility, no noise).

    `key` must be exactly the value the DB is opened with (the stripped file
    key); it is stored verbatim so a later read compares equal.

    Returns one of the MIRROR_* constants.
    """
    try:
        if not is_available(timeout=timeout):
            return MIRROR_SKIPPED
        if get_key(service=service, name=name, timeout=timeout) == key:
            return MIRROR_SYNCED
        set_key(key, service=service, name=name, timeout=timeout)
        # Confirm the write really landed before claiming success.
        if get_key(service=service, name=name, timeout=timeout) == key:
            return MIRROR_DONE
        return MIRROR_ERROR
    except Exception:
        # Golden rule: a keychain hiccup must never break the boot.
        return MIRROR_ERROR
