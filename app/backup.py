"""Sovereign backup/restore v1 — a BIP39 recovery phrase wraps the SQLCipher key.

Invariants: NO plaintext ever leaves; the user holds the recovery phrase; Vokter never holds it.
Respects §0.6 unlinkable-by-default — a BIP39 phrase is NOT an identity (no npub, zero linkage to the
user's social key). Wrap-K with a pluggable recipient (v1 = BIP39; Nostr-wrap deferred).

Shape: the SQLCipher key K (raw 32 bytes) encrypts the DB. A recovery phrase (256-bit BIP39) →
scrypt KEK → AES-256-GCM-wraps K → the wrapped-K blob lives in the backup MANIFEST. Export is a
CONSISTENT snapshot (`VACUUM INTO`, not cp) + manifest + a verify-at-export that actually opens the
copy. Restore reconstructs K from (backup + phrase) ALONE; a wrong phrase fails cleanly (GCM auth tag).

NOTE: this module is the backup CORE. The production db.py/keysource raw-key integration + the EN/ES
UI are separate pieces (see vokter-internal/BACKUP_RESTORE_DESIGN.md).
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MANIFEST_VERSION = 1
# scrypt KEK params — ~128 MiB, deliberately expensive (a real KDF, not a bare hash). The phrase
# already carries 256 bits, so this is belt-and-suspenders against a weak-RNG phrase, not the sole
# defence. Params are RECORDED in the manifest so a future restore reproduces them exactly.
_SCRYPT = {"n": 1 << 17, "r": 8, "p": 1}


class BackupError(Exception):
    """Any backup/restore failure (bad phrase, corrupt backup, wrong key)."""


# ───────────────────────────── BIP39 (self-contained) ─────────────────────────────
def _find(name: str) -> str:
    here = os.path.dirname(__file__)
    for p in ([os.path.join(getattr(sys, "_MEIPASS", ""), name)] if getattr(sys, "_MEIPASS", None) else []) \
            + [os.path.join(here, name)]:
        if p and os.path.isfile(p):
            return p
    raise BackupError(f"{name} not found (BIP39 wordlist missing from the bundle)")


_WORDS: list[str] | None = None
def _wordlist() -> list[str]:
    global _WORDS
    if _WORDS is None:
        _WORDS = open(_find("bip39_english.txt"), encoding="utf-8").read().split()
        if len(_WORDS) != 2048:
            raise BackupError(f"BIP39 wordlist must be 2048 words, got {len(_WORDS)}")
    return _WORDS


def _bits(data: bytes) -> str:
    return "".join(f"{b:08b}" for b in data)


def entropy_to_mnemonic(entropy: bytes) -> list[str]:
    """Standard BIP39: entropy (16/32 bytes) + SHA256 checksum → 11-bit word indices."""
    if len(entropy) not in (16, 32):
        raise BackupError("entropy must be 16 or 32 bytes (12/24 words)")
    cs_len = len(entropy) * 8 // 32
    bits = _bits(entropy) + _bits(hashlib.sha256(entropy).digest())[:cs_len]
    wl = _wordlist()
    return [wl[int(bits[i:i + 11], 2)] for i in range(0, len(bits), 11)]


def mnemonic_to_entropy(words: list[str]) -> bytes:
    """Reverse of entropy_to_mnemonic, with checksum verification (raises on tamper/typo)."""
    wl = _wordlist()
    idx = {w: i for i, w in enumerate(wl)}
    try:
        bits = "".join(f"{idx[w]:011b}" for w in words)
    except KeyError as e:
        raise BackupError(f"not a valid recovery word: {e.args[0]!r}")
    ent_len = len(bits) * 32 // 33
    if ent_len % 8 or ent_len // 8 not in (16, 32):
        raise BackupError("recovery phrase must be 12 or 24 words")
    entropy = int(bits[:ent_len], 2).to_bytes(ent_len // 8, "big")
    want = _bits(hashlib.sha256(entropy).digest())[: len(bits) - ent_len]
    if bits[ent_len:] != want:
        raise BackupError("recovery phrase checksum failed (a word is wrong or out of order)")
    return entropy


# ───────────────────────────── KEK + wrap/unwrap ─────────────────────────────
def derive_kek(mnemonic: list[str], salt: bytes, params: dict | None = None) -> bytes:
    p = params or _SCRYPT
    seed = " ".join(mnemonic).encode("utf-8")
    return Scrypt(salt=salt, length=32, n=p["n"], r=p["r"], p=p["p"]).derive(seed)


def wrap_k(k: bytes, kek: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(kek).encrypt(nonce, k, b"vokter-backup-v1")
    return {"nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def unwrap_k(blob: dict, kek: bytes) -> bytes:
    try:
        return AESGCM(kek).decrypt(base64.b64decode(blob["nonce"]),
                                   base64.b64decode(blob["ct"]), b"vokter-backup-v1")
    except Exception:
        raise BackupError("that recovery phrase does not match this backup")


# ───────────────────────────── SQLCipher helpers (raw-key) ─────────────────────────────
def _open_raw(path: str, k: bytes):
    import sqlcipher3.dbapi2 as sq
    conn = sq.connect(path)
    conn.execute("PRAGMA cipher_memory_security = ON")
    conn.execute('PRAGMA key = "x\'%s\'"' % k.hex())   # raw-key mode: the 32 bytes ARE the key
    return conn


def _open_passphrase(path: str, passphrase: str):
    """Open a passphrase-mode DB exactly like the LIVE db.py path (`PRAGMA key='…'`). Used ONLY to
    read the live DB for the snapshot — the live DB is never modified."""
    import sqlcipher3.dbapi2 as sq
    conn = sq.connect(path)
    conn.execute("PRAGMA cipher_memory_security = ON")
    conn.execute("PRAGMA key = '%s'" % passphrase.replace("'", "''"))
    return conn


def _key_opens(path: str, k: bytes) -> bool:
    """keysource-style: does K open this DB? (the restore-validation gate)."""
    try:
        c = _open_raw(path, k)
        c.execute("SELECT count(*) FROM sqlite_master").fetchone()
        c.close()
        return True
    except Exception:
        return False


# ───────────────────────────── set up recovery ─────────────────────────────
def setup_recovery(words: int = 12) -> tuple[list[str], bytes, bytes]:
    """Establish the recovery secret: a BIP39 phrase (words=12 → 128-bit, friendlier, DEFAULT; or
    24 → 256-bit) → scrypt KEK (+ salt). The caller shows the phrase ONCE and stores (salt, KEK)
    locally — the KEK is DERIVABLE from the phrase, so losing the machine loses nothing. The phrase
    itself is never persisted. Returns (mnemonic, salt, kek)."""
    if words not in (12, 24):
        raise BackupError("words must be 12 or 24")
    mnemonic = entropy_to_mnemonic(secrets.token_bytes(16 if words == 12 else 32))
    salt = secrets.token_bytes(16)
    return mnemonic, salt, derive_kek(mnemonic, salt)


# ───────────────────────────── export (rekey-copy-only; live DB untouched) ─────────────────────────
def export_backup(live_db_path: str, live_passphrase: str, out_db_path: str, kek: bytes, salt: bytes,
                  schema_version: int = MANIFEST_VERSION) -> dict:
    """Write a CONSISTENT encrypted snapshot of the LIVE DB and wrap its key to the recovery KEK.
    REKEY-COPY-ONLY model (zero blast radius to existing users): the live DB is READ with its
    PASSPHRASE (never modified); `VACUUM INTO` makes a consistent copy (handles WAL/in-flight writes);
    the COPY is re-keyed to a fresh RAW key K_b; K_b is AES-GCM-wrapped to `kek`. Verifies the copy
    opens with K_b before returning. Returns the manifest (wrapped K_b + salt + cipher — never K_b)."""
    if os.path.exists(out_db_path):
        raise BackupError(f"refusing to overwrite existing backup at {out_db_path}")
    src = _open_passphrase(live_db_path, live_passphrase)
    try:
        cipher_version = src.execute("PRAGMA cipher_version").fetchone()[0]
        src.execute("VACUUM INTO '%s'" % out_db_path.replace("'", "''"))   # copy keeps the live passphrase key
    finally:
        src.close()

    # Re-key the COPY (NOT the live DB) from passphrase → a fresh raw key. The live install is untouched.
    k_b = secrets.token_bytes(32)
    cp = _open_passphrase(out_db_path, live_passphrase)
    try:
        cp.execute('PRAGMA rekey = "x\'%s\'"' % k_b.hex())
        cp.commit()
    finally:
        cp.close()

    manifest = {
        "vokter_backup_version": MANIFEST_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": schema_version,
        "cipher": {"version": cipher_version, "mode": "raw-key"},
        "recovery": {"method": "bip39", "kdf": "scrypt", "kdf_params": dict(_SCRYPT),
                     "salt": base64.b64encode(salt).decode(), "wrap": "aes-256-gcm",
                     "wrapped_k": wrap_k(k_b, kek)},
    }
    with open(out_db_path + ".manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # VERIFY-AT-EXPORT: the copy must open with the RAW K_b and pass an integrity check — "wrote a
    # file" is not "wrote a decryptable, consistent file." A failure raises BEFORE the backup is good.
    v = _open_raw(out_db_path, k_b)
    try:
        bad = v.execute("PRAGMA cipher_integrity_check").fetchall()
        if bad:
            raise BackupError(f"backup failed integrity check: {bad[:3]}")
        v.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        v.close()
    return manifest


# ───────────────────────────── restore ─────────────────────────────
def restore_key(backup_db_path: str, manifest: dict, mnemonic: list[str]) -> bytes:
    """Reconstruct K from (backup + phrase) ALONE and validate it opens the backup. Raises BackupError
    on a wrong phrase (GCM auth) or a key that doesn't open the DB. Returns K (the caller then seeds the
    new keychain + places the DB — reusing keysource)."""
    rec = manifest["recovery"]
    if rec.get("method") != "bip39":
        raise BackupError(f"unsupported recovery method: {rec.get('method')}")
    salt = base64.b64decode(rec["salt"])
    kek = derive_kek(mnemonic, salt, rec.get("kdf_params"))
    k = unwrap_k(rec["wrapped_k"], kek)              # clean failure on wrong phrase
    if not _key_opens(backup_db_path, k):
        raise BackupError("recovered key did not open the backup (corrupt backup or wrong manifest)")
    return k
