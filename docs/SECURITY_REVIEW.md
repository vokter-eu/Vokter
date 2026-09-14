# Security Review — DB-key exposure hardening (TODO #4)

**Status:** Fixed, with a documented residual.
**Scope:** How Vokter's SQLCipher database key is exposed while the backend runs,
what we closed, and the ceiling we accept.

## Background — how the key reaches the backend

The database is encrypted with SQLCipher. The orchestrator decides the key
source (keychain-first, file fallback — see `desktop/keysource.py`) and hands the
chosen key to the backend process as the `VOKTER_DB_KEY` environment variable;
`app/config.py` reads it at import (`config.py:58`). The key is deliberately
passed via the **environment, never on `argv`** — the `--verify-key` probe also
reads it from the environment (`VOKTER_DB_KEY`; the dev-path validator in
`keysource.py` uses `VOKTER_VERIFY_KEY`), never from the command line — so it does
not appear in the bare process list.

On a user machine the backend is always the **frozen** binary — the orchestrator
launches `flavour == "frozen"` as a fresh `execve` of the packaged executable
(`desktop/orchestrator.py`), so the entry point in `desktop/freeze/vokter_backend.py`
runs on every boot.

## Threat — same-user `/proc` inspection

Passing the key via the environment (and holding it in process memory, as any
process that uses a secret must) opens a window to **any other process running
as the same user**:

- `/proc/<pid>/environ` — reads the child's environment, including `VOKTER_DB_KEY`.
- `ps eww <pid>` — same thing, via the same `/proc` file.
- `/proc/<pid>/mem`, `/proc/<pid>/maps`, and `ptrace(2)` — read the key straight
  out of the running process's memory.

By default a Linux process is *dumpable*, so all of the above are readable by the
process owner. That means a non-privileged, same-user program could lift the DB
key from a running Vokter without ever touching the disk.

## Fix — `prctl(PR_SET_DUMPABLE, 0)` at process start

`desktop/freeze/vokter_backend.py` marks the process **non-dumpable** as the very
first thing it does (`_harden_non_dumpable()`, called at module load, before
`config` is imported and the key is read):

```python
ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, ...)
```

Clearing the dumpable flag makes the kernel:

- re-own `/proc/<pid>/{environ,mem,maps,...}` to `root:root` with mode `0400`, so
  the **same user can no longer read them**, and
- **refuse same-user `ptrace`** attaches.

This is the standard pattern for secret-holding daemons (`ssh-agent`,
`gpg-agent`). It closes both the environment window (`/proc/environ`, `ps eww`)
and the process-memory window (`/proc/mem`, `ptrace`) in one call.

Properties of the implementation:

- **Runs before the key is read.** The call is at module load (line 46), ahead of
  the `from config import ...` at the `__main__` entry, so exposure shrinks from
  the backend's whole runtime to a sub-second startup window (the interval before
  the flag is set, during which nothing sensitive is in memory yet).
- **Re-applied on every mode.** The dumpable flag resets to 1 on each `execve`, so
  the module-level call re-hardens the server child, the `--verify-key` probe, and
  `--orchestrate` alike.
- **Linux-guarded.** `prctl` is Linux-only; on other platforms the function is a
  silent no-op, so a future Windows/macOS build is unaffected.
- **Best-effort.** The `prctl` call is wrapped in `try/except`; hardening must
  never block boot. If it fails we fall back to the pre-existing (dumpable)
  behaviour rather than refusing to start.

### Verification

Measured on Linux, same user, in two steps.

**1 — Mechanism (control vs. treatment).** A probe that executes the real
`_harden_non_dumpable()` vs. an identical process without it, both holding a
distinctive `VOKTER_DB_KEY`, proves the leak is real and that the fix closes it:

| Check | Control (dumpable) | Treatment (non-dumpable) |
| --- | --- | --- |
| `/proc/<pid>/environ` | key **readable** | owner `root:root`, mode `-r--------`, **permission denied** to same user |
| `ps eww <pid>` | key **visible** | key **absent** |

**2 — Real backend.** The actual frozen entry point (`vokter_backend.py` → the
real `config` + `main` + `uvicorn.run`) was booted with `VOKTER_DB_KEY` set. It
came up normally and every same-user read path was closed:

| Check | Result on the running backend |
| --- | --- |
| `GET /` | **HTTP 200** — boots and serves normally |
| `/proc/<pid>/environ` | `root:root -r--------` → **permission denied** (env window closed) |
| `ps eww <pid>` | key **hidden** |
| `ptrace` attach (`gdb -p`, same user) | **denied** (process-memory window closed) |

Together these confirm the fix closes the environment, process-list, and
process-memory/`ptrace` read paths while the real backend still boots and serves
(`prctl` runs before any backend import and is best-effort, so it cannot block
boot).

## Accepted residual — same-user-at-rest access

This fix removes the *running-process* read paths. It does **not**, and cannot,
defend against an attacker who is already executing code as the same user, because
the trust boundary is the user account itself:

- **The file-fallback key is owner-readable at rest.** When the OS keychain is
  unavailable, the key is stored at `DATA_DIR/.db_key`
  (`~/.local/share/vokter/.db_key` on a packaged install), created with mode
  `0600` (`_write_key_file_excl`). Same-user code can read that file directly.
- **Same-user malware wins regardless.** A program running as the user can
  keylog, read the file-fallback key, replace the Vokter binary or its Python
  with a trojaned copy, or scrape the key at the moment the app itself reads it.
  No in-process hardening can stop an attacker who owns the account.

In other words, `PR_SET_DUMPABLE=0` raises the bar from "any same-user process can
passively snapshot the key from a running backend" to "an attacker must already be
running code as the user *and* target the at-rest key or the binary." Defending
past that point (encrypting the at-rest key, binary integrity, keylogging) is out
of scope for a local single-user application and belongs to OS-level protections
(full-disk encryption, keychain-backed storage, account isolation).

## Conclusion

TODO #4 is **closed as fixed-with-documented-residual**. The same-user
`/proc` env + process-memory + `ptrace` read paths against the running backend are
closed by `PR_SET_DUMPABLE=0`; the remaining exposure is same-user-at-rest access,
which is the account trust boundary and is accepted.
