"""C2a — el bit-guard de conversaciones (ownership por sesión humana).

Prueba la invariante que importa, como el gate de memoria, sin red ni modelo:
  * Un caller solo ve filas de su PROPIA clase (`human_owned`): un no-humano con el conv_id
    del humano obtiene CERO filas → indistinguible de un id inexistente (sin oráculo).
  * Anti-inyección: una escritura no-humana en el conv_id del humano no entra en el
    historial que el humano carga.
  * Los DOS lectores cubiertos: `_load_history` (/api/ask) y `_recent_user_context`
    (/api/memory/suggest), y `/api/memory/suggest` deny-by-default para un caller no-humano.
  * Migración idempotente + backfill B: una tabla vieja (sin la columna) → ALTER añade
    `human_owned`, el UPDATE marca TODAS las filas existentes = 1, la DB abre, filas intactas,
    y correrlo otra vez es no-op.

Ejecutar:  desktop/runtime/venv/bin/python tests/conversation_gate_test.py
(ver docs/SECURITY_REVIEW.md C2a y /home/harry/vokter-C2-analysis.md)
"""
import asyncio
import os
import sqlite3
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="vokter_convgate_test_")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "main.db")
os.environ.pop("VOKTER_DB_KEY", None)                       # DB plana (sin SQLCipher) para el test
os.environ["VOKTER_HUMAN_SESSION_TOKEN"] = "TESTTOKEN_conv_cafe"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import chat
import db as dbmod
import memory_routes
from memory_routes import SuggestIn


def _seed():
    chat._save_turn("conv-H", "human secret question", "human secret answer", human=True)
    chat._save_turn("conv-P", "peer question", "peer answer", human=False)


def test_load_history_reader_gate():
    _seed()
    h = chat._load_history("conv-H", 10, human=True)
    assert any("human secret" in m["content"] for m in h), "human must read its own thread"
    nh = chat._load_history("conv-H", 10, human=False)
    assert nh == [], f"non-human must NOT read the human thread, got {nh!r}"
    p = chat._load_history("conv-P", 10, human=False)
    assert any("peer" in m["content"] for m in p), "peer must read its own thread"


def test_no_oracle():
    denied = chat._load_history("conv-H", 10, human=False)               # exists, but human's
    missing = chat._load_history("conv-DOES-NOT-EXIST", 10, human=False)  # does not exist
    assert denied == missing == [], "denied and non-existent must be indistinguishable"


def test_anti_injection():
    # A peer (human_owned=0) writes into the HUMAN's conv_id.
    chat._save_turn("conv-H", "INJECTED peer turn", "INJECTED answer", human=False)
    h = chat._load_history("conv-H", 50, human=True)
    assert not any("INJECTED" in m["content"] for m in h), "peer injection leaked into human history"
    nh = chat._load_history("conv-H", 50, human=False)
    assert not any("human secret" in m["content"] for m in nh), "human rows leaked to non-human"
    assert any("INJECTED" in m["content"] for m in nh), "peer sees only its own rows"


def test_recent_user_context_only_human_rows():
    ctx = memory_routes._recent_user_context("conv-H", "unrelated")
    assert any("human secret" in c for c in ctx), "must read the human's own user turns"
    assert not any("INJECTED" in c for c in ctx), "peer-owned rows must be excluded"


def test_memory_suggest_denies_non_human():
    for mark in (None, "", "wrong-token"):
        out = asyncio.run(memory_routes.memory_suggest(
            SuggestIn(message="I love green tea", conversation_id="conv-H"),
            x_vokter_human_session=mark,
        ))
        assert out == {"suggestions": []}, f"non-human suggest must propose nothing (mark={mark!r})"


def test_migration_backfill_and_idempotent():
    # Simulate a PRE-migration DB: conversations table WITHOUT human_owned, with real rows.
    old = os.path.join(_TMP, "old.db")
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE conversations (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
              "conv_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL)")
    c.execute("INSERT INTO conversations(conv_id,role,content,ts) VALUES('old','user','old turn',1.0)")
    c.execute("INSERT INTO conversations(conv_id,role,content,ts) VALUES('old','assistant','old answer',1.0)")
    c.commit()
    c.close()

    saved = dbmod.DB_PATH
    dbmod.DB_PATH = old                       # point the REAL migration (get_db) at the old file
    try:
        conn = dbmod.get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        assert "human_owned" in cols, "migration must ADD the human_owned column"
        n = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        h = conn.execute("SELECT COUNT(*) FROM conversations WHERE human_owned=1").fetchone()[0]
        assert n == 2, f"rows must survive migration (got {n})"
        assert h == 2, f"backfill B must mark ALL {n} existing rows human_owned=1 (got {h})"
        content = [r[0] for r in conn.execute("SELECT content FROM conversations ORDER BY seq").fetchall()]
        assert content == ["old turn", "old answer"], f"existing rows must be intact, got {content}"
        conn.close()

        # Idempotent: run again → no error, exactly one column, counts unchanged.
        conn2 = dbmod.get_db()
        cols2 = [r[1] for r in conn2.execute("PRAGMA table_info(conversations)").fetchall()]
        assert cols2.count("human_owned") == 1, "re-running init must not duplicate the column"
        h2 = conn2.execute("SELECT COUNT(*) FROM conversations WHERE human_owned=1").fetchone()[0]
        assert h2 == 2, "idempotent: backfill must not run again / change counts"
        conn2.close()
    finally:
        dbmod.DB_PATH = saved


def main():
    test_load_history_reader_gate()
    test_no_oracle()
    test_anti_injection()
    test_recent_user_context_only_human_rows()
    test_memory_suggest_denies_non_human()
    test_migration_backfill_and_idempotent()
    print("OK — conversation gate: reader gated (non-human sees zero human rows, no oracle), "
          "anti-injection holds, both readers covered + suggest deny-by-default, and the "
          "migration adds human_owned with a one-time backfill=1, intact & idempotent.")


if __name__ == "__main__":
    main()
