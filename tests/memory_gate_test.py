"""Lote de seguridad #1 — el gate de memoria personal (P2).

Prueba la invariante que importa, como función pura, sin red ni modelo:
  * CON marca humana  → el prompt del sistema es BYTE-IDÉNTICO a "hoy"
                        (build_system_prompt(cfg) + memory.system_block()).
  * SIN marca humana  → la memoria se RETIENE; el prompt es el baseline sin memoria
                        (build_system_prompt(cfg)) — un par/MCP nunca ve los hechos.
  * is_local_human_session: deny-by-default (None/vacío/incorrecto → False; sin token
                        configurado → False, estricto), sólo el token exacto → True.

Ejecutar:  desktop/runtime/venv/bin/python tests/memory_gate_test.py
(ver docs/threat-model-prompt-injection.md §7-8)
"""
import asyncio
import os
import sys
import tempfile

# El env debe fijarse ANTES de importar módulos respaldados por config (config lee env
# al importarse). DB sqlite PLANA (sin VOKTER_DB_KEY) y token conocido para el test.
_TMP = tempfile.mkdtemp(prefix="vokter_memgate_test_")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "test.db")
os.environ.pop("VOKTER_DB_KEY", None)
os.environ["VOKTER_HUMAN_SESSION_TOKEN"] = "TESTTOKEN_deadbeefcafe"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agent_config
import chat
import memory

FACT = "El usuario prefiere el té verde por la mañana <b>importante</b>"


def test_is_local_human_session():
    assert chat.is_local_human_session(None) is False
    assert chat.is_local_human_session("") is False
    assert chat.is_local_human_session("wrong-token") is False
    assert chat.is_local_human_session("TESTTOKEN_deadbeefcafe") is True

    # No token configured (raw dev/docker, no Electron) → strict deny, no match possible.
    saved = chat.HUMAN_SESSION_TOKEN
    chat.HUMAN_SESSION_TOKEN = ""
    try:
        assert chat.is_local_human_session("") is False
        assert chat.is_local_human_session("anything") is False
    finally:
        chat.HUMAN_SESSION_TOKEN = saved


def test_byte_identical_and_withheld():
    cfg = agent_config.get_config()
    base = agent_config.build_system_prompt(cfg)

    # build_chat_system is async + query-aware now; query=None keeps the Phase-1b dump-all
    # (system_block) so THIS invariant — the P2 gate — is unchanged. (Query-aware retrieval
    # is exercised by tests/memory_retrieval_eval.py, not here.)
    run = asyncio.run

    # No facts yet: both paths reduce to the baseline (system_block() == "").
    assert run(chat.build_chat_system(cfg, human=False)) == base
    assert run(chat.build_chat_system(cfg, human=True)) == base + memory.system_block()

    # Seed a real fact (with HTML in it — proves the gate is about WHO, not sanitising).
    memory.add(FACT, source="told")
    assert FACT in memory.system_block()

    human_prompt = run(chat.build_chat_system(cfg, human=True))
    peer_prompt = run(chat.build_chat_system(cfg, human=False))

    # Bilal's exact ask: WITH the human mark, byte-identical to today's concatenation.
    assert human_prompt == agent_config.build_system_prompt(cfg) + memory.system_block()

    # The human sees the fact; a non-human caller (peer/MCP) does NOT — and its prompt
    # is byte-identical to a memory-less Vokter.
    assert FACT in human_prompt
    assert FACT not in peer_prompt
    assert peer_prompt == agent_config.build_system_prompt(cfg)


def main():
    test_is_local_human_session()
    test_byte_identical_and_withheld()
    print("OK — memory gate: byte-identical WITH mark, withheld WITHOUT mark, "
          "deny-by-default (no token → no memory)")


if __name__ == "__main__":
    main()
