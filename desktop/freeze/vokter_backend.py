"""Frozen entry point — serve the real Vokter backend with uvicorn.

Almost all configuration is env-driven and lives in config.py, including the
bind address (VOKTER_BIND/VOKTER_PORT). The ONE command-line mode is
`--verify-key <db>`: on a user machine this frozen binary is the only piece
that carries sqlcipher3, so the orchestrator (whose own interpreter has no
sqlcipher3) shells out to it to answer "does this key open the DB?" before
choosing a key source. That check must have NO side effects, so it runs BEFORE
importing config/main (which enforce fail-closed encryption and create dirs).
"""
import sys


# Capability marker — printed FIRST so the caller can tell a binary that
# understands --verify-key from an old one that would boot the server instead.
# Kept in lock-step with keysource._VERIFY_MARKER.
_VERIFY_MARKER = "__VOKTER_VERIFY_KEY__"


def _verify_key() -> int:
    """`--verify-key <db>`: exit 0 iff VOKTER_DB_KEY opens <db> (read-only),
    non-zero otherwise. Prints the capability marker to stdout first. No
    config/main import, no side effects, never binds a socket.

    Kept in lock-step with keysource._VERIFY_SCRIPT (the dev/venv path)."""
    import os

    print(_VERIFY_MARKER, flush=True)  # prove capability before anything can fail
    import sqlcipher3.dbapi2 as s

    args = sys.argv[sys.argv.index("--verify-key") + 1:]
    if not args:
        print("verify-key: missing <db> path", file=sys.stderr)
        return 2
    db = args[0]
    key = os.environ.get("VOKTER_DB_KEY", "")
    try:
        con = s.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        con.execute("PRAGMA key='%s'" % key.replace("'", "''"))
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        con.close()
        return 0
    except Exception as exc:  # wrong key, unreadable DB, anything → not usable
        print(f"verify-key: cannot open {db}: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if "--verify-key" in sys.argv:
        sys.exit(_verify_key())

    import multiprocessing

    import uvicorn

    from config import BIND, PORT
    from main import app

    # No-op on Linux; on Windows/macOS (spawn) it stops a re-executed frozen
    # exe from booting a second server when a dependency spawns a process.
    multiprocessing.freeze_support()
    uvicorn.run(app, host=BIND, port=PORT, log_level="info")
