# SECURITY_REVIEW — procedimiento de auditoría de egreso

Documento vivo y **accionable**, no ceremonial. Existe porque el bug de la memoria
(Fase 1b) filtrable a A2A (Fase 6) nació de la **interacción** entre dos piezas
construidas en momentos distintos: cada una se revisó en su diff, el agujero no estaba
en ningún diff. La defensa es mirar el sistema **en conjunto**, por celdas de una matriz
dato×canal, no pieza a pieza.

Trazado del código el 2026-07-28 (no de memoria). Cuando toques un canal o un dato,
**re-traza la celda** y actualiza aquí.

---

## 1. La matriz — dato sensible × canal de salida

Estado REAL hoy. Leyenda:

- ✅ **gateado + test** — mecanismo que lo impone Y test que lo prueba.
- ☑️ **gateado, SIN test** — invariante afirmada e impuesta por código, pero sin test que la fije (deuda: ver §3).
- 🔒 **nunca egresa** — por construcción no sale por ningún canal.
- 🟡 **hueco ACEPTADO** — mirado y aceptado con razón (§4).
- 🔶 **surgido en auditoría** — hallado aquí, decisión de Bilal pendiente (§4).
- — **N/A** — el canal no puede alcanzar ese dato.

| Dato \ Canal        | A2A (HTTP)     | Nostr (NIP-17) | MCP (stdio/SSE) | Browse (out)   | Scheduler      | Email          | Loopback 127.0.0.1 |
|---------------------|----------------|----------------|-----------------|----------------|----------------|----------------|--------------------|
| **Memoria personal**| ✅ retenida     | ✅ retenida     | ✅ retenida      | —              | ✅ retenida     | —              | ✅ retenida         |
| **Documentos**      | ☑️ solo trusted | ☑️ solo trusted | por diseño (host)| —              | local (no sale)| — (es entrada) | 🟡 §8.7             |
| **Conversaciones**  | ☑️ por peer     | ☑️ por peer     | 🔶 sin ownership | —              | local          | —              | 🟡 §8.7 + 🔶        |
| **Llaves** (DB/priv)| 🔒             | 🔒             | 🔒              | 🔒 (§)         | 🔒             | 🔒             | 🔒                 |
| **Tokens** (admin/A2A/humano)| 🔒    | 🔒             | 🔒              | 🔒             | 🔒             | 🔒             | 🔒                 |

### Detalle por celda (mecanismo · test)

- **Memoria personal — TODOS los canales de agente + loopback → RETENIDA.** Único punto de
  inyección: `memory.system_block()` se llama **solo** en `chat.py`, vía
  `chat.build_chat_system(cfg, human)`, y `human` solo es `True` si la petición trae el token
  de sesión humana (`X-Vokter-Human-Session`, acuñado por Electron 1×/lanzamiento). Los
  adaptadores (A2A/Nostr/MCP) y el scheduler hablan con la API local con el **token admin**,
  no el humano → `human=False` → memoria retenida (`build_chat_system` == baseline).
  Scheduler va por `planner._execute`, que **nunca** llama `system_block`. **Esta es la celda
  más fuerte del sistema** (justo lo que cerramos hoy) y contrasta con la fila de Documentos.
  **Test:** `tests/memory_gate_test.py` — a nivel de FUNCIÓN (`build_chat_system` directo);
  **falta la validación por la ruta HTTP real** (que `Header(...)` → `human=False`) y la
  causal-visual en VM (ver §4, coherente con threat-model §8.8).

- **Documentos — A2A/Nostr → solo peers TRUSTED.** Frontera en `agent_dispatch.dispatch_message`:
  verbos públicos (`introduce/hello/whoami`) devuelven solo la tarjeta pública; TODO lo demás
  (`ask`/`browse`/`plan`/`wallet_*`) exige `trusted=True`, y el **default es `False`
  (fail-closed)**. Trust: A2A = bearer `A2A_TOKEN` (compare_digest; sin token → False); Nostr =
  remitente firmado en allowlist o rating `trusted`. **Test: NINGUNO** → ver §3, invariante #2.
- **Documentos — MCP → por diseño al host.** MCP es un adaptador separado (`mcp_server.py`,
  stdio/SSE) que se autentica con el token admin; quien lo lanza (config de Claude Desktop, etc.)
  lo autoriza implícitamente. Expone `ask` (docs+fuentes), `plan`, y **`wallet_send`** (ver §4).
- **Documentos — Loopback → 🟡 §8.7.** Cualquier proceso local puede pegar a `/api/ask` y recibir
  respuestas de documentos. Aceptado: "máquina comprometida = límite declarado".
- **Conversaciones — siloadas, pero solo por UUID.** `/api/ask` hace
  `conv_id = q.conversation_id or uuid4()` **sin comprobación de propiedad**. En A2A el `conv_id`
  lo controla el dispatcher (mapa `context_key→conv_id`), pero en MCP `ask(question, conversation_id)`
  el `conversation_id` lo pone el LLAMANTE. El siloing depende **solo de que el UUID sea
  inadivinable**, no de ownership → 🔶 §4.
- **Llaves — 🔒 nunca.** Clave SQLCipher: keychain/env, nunca serializada. Clave privada de
  identidad: local; **solo la clave PÚBLICA** va en la tarjeta pública (por diseño — es tu
  dirección). Browse: "una session key efímera por request, guardada local, nunca sale".
  **Test: ninguno** (negativo) — ver §3, invariante #4.
- **Tokens — 🔒 nunca.** admin/A2A/humano se **comparan**, jamás se devuelven en una respuesta.
  **Test: ninguno** — §3, invariante #4.
- **Browse (columna) — egreso = solo la URL.** Salida hacia fuera = un GET a la URL. Guardas SSRF:
  `_is_private_host`/`_internal_addr` (bloqueo de hosts privados, consciente de DNS-rebinding) +
  `_is_allowed` (allowlist de patrones). No egresa dato personal; el riesgo es SSRF/exfil-por-URL,
  mitigado por allowlist.
- **Email (columna) — entrada, no salida.** `email_connector` (router incluido en `main.py`,
  tras la puerta admin H1) ingiere correo → se vuelve **Documentos** (misma fila). No hay canal
  de envío saliente hoy. Si algún día se añade envío, es un canal NUEVO → dispara §2.

---

## 2. Disparadores (por EVENTO, no por calendario)

El calendario se salta; los eventos no. Audita cuando:

1. **Nuevo tipo de dato sensible.** (La memoria personal fue uno y no disparó nada — ese fue el fallo.)
2. **Nuevo canal de entrada o de salida** (protocolo, tool MCP, conector, transporte).
3. **Antes de un release con usuarios reales** → auditoría COMPLETA (toda la matriz). **Bloqueante
   de release:** escribir el test de la frontera de confianza (§4 · C1) — no se lanza con esa
   invariante sin test.

Un cambio que toque `agent_dispatch`, `chat.build_chat_system`, `memory.system_block`, o
cualquier adaptador (`a2a_server`/`nostr_*`/`mcp_server`/`email_connector`/`scheduler`) cuenta
como disparador 1 o 2.

---

## 3. Regla de invariantes: afirmar ⇒ testear

**Toda invariante de seguridad afirmada debe tener un test que la imponga. Si no se puede
testear, no se afirma en un comentario** — un comentario que promete sin imponer es confianza
injustificada (el "nunca en A2A" de `memory.py` afirmaba sin imponer; ese fue el bug).

Inventario de invariantes afirmadas hoy:

| # | Invariante | ¿Impuesta por? | ¿Test? |
|---|------------|----------------|--------|
| 1 | Memoria personal solo a la sesión humana local | `build_chat_system`+token humano; `system_block` solo en chat.py | ✅ `memory_gate_test.py` (nivel función; ruta HTTP pendiente) |
| 2 | **Frontera de confianza: caller no-trusted → solo tarjeta pública; verbos privados exigen `trusted=True` (fail-closed)** | `agent_dispatch.dispatch_message` | ❌ **NINGUNO** ← 2ª instancia de la MISMA clase de bug de hoy: el docstring afirma que hace "dataSharing: none-without-permission" verdad *en el endpoint*, sin test que lo fije. **Máxima prioridad de test.** |
| 3 | `system_block` tiene un único punto de inyección (chat.py) | arquitectura (grep confirma 1 llamante) | ❌ sin test-guardia contra un llamante nuevo |
| 4 | Llaves privadas y tokens nunca egresan | serialización (nunca se devuelven) | ❌ sin test (negativo; factible como test-grep de responses) |
| 5 | Browse no alcanza hosts privados (SSRF) | `_is_private_host`/`_is_allowed` | ❌ no se encontró test |

---

## 4. Huecos — tres cubos (distinguir "no lo miramos" de "lo miramos")

**A. ACEPTADOS (deliberados, con razón):**
- **§8.7 — backend en `127.0.0.1:PORT` accesible por procesos locales.** Docs y conversaciones
  quedan expuestos a cualquier proceso local (la memoria NO — está gateada incluso ahí). Razón:
  "máquina comprometida = límite declarado".
- **AppImage con sandbox degradado.** Corre de `/tmp` ≠ `/opt/Vokter` → el perfil AppArmor
  (clavado a ruta) no lo cubre; muere fail-closed antes del backend. El `.deb` es el camino con
  sandbox pleno; AppImage es plan-B portable documentado.

**B. PENDIENTE DE VALIDACIÓN (lógica verificada, falta confirmación):**
- **Gate de memoria — validación causal VISUAL en VM.** Verificado por test (función) + arranque
  del backend congelado sirviendo el código nuevo; falta ver el token viajando por IPC en la app
  real y el aviso fail-closed pintado. Threat-model §8.8. Si falla, será cableado del token, no la
  lógica.

**C. SURGIDOS EN ESTA AUDITORÍA → registrados, NO resueltos (revisado por Bilal 2026-07-28):**

- **C3 · MCP `wallet_send` sin gate real de confirmación — EL MÁS PRIORITARIO.** La confirmación
  es solo una INSTRUCCIÓN al host MCP, que el host puede ignorar → no es confirmación, es una
  sugerencia. **Viola un principio explícito de Bilal: confirmación humana para TODA acción con
  consecuencias.** Para mover dinero, una sugerencia no basta. **Pista de diseño (para cuando se
  ataque):** ¿puede el backend exigir una confirmación que NO dependa del host — p.ej. que
  `wallet_send` requiera el **token de sesión humana**, igual que la memoria (§1)? Si el mismo
  mecanismo del gate de memoria sirve para gatear wallet, es coherente y reutiliza lo que ya
  funciona. NO arreglar ahora; registrado con la pista.

- **C2 · `conversation_id` sin comprobación de propiedad — PENDIENTE-DE-ANÁLISIS.** El siloing de
  conversaciones depende solo de que el UUID sea inadivinable; "inadivinable" es defensa débil para
  datos sensibles (los UUIDs se filtran). Antes de gastar en el arreglo, hay que ver el **vector
  real**. Preguntas a responder:
  - (a) ¿un peer trusted puede **ENUMERAR** conv_ids, o solo leer uno que ya conozca?
  - (b) **¿dónde se filtran** los conv_ids — logs, respuestas de la API, tarjeta pública?
  - (c) ¿el ownership check es **caro** o es un simple `WHERE user_owns(conv_id)`?
  No resuelto: primero el análisis del vector, luego la decisión (ownership check vs aceptar).

- **C1 · Frontera de confianza (invariante #2) sin test — DISPARADOR ANTES DEL PRÓXIMO RELEASE.**
  Es el hueco que la matriz cazó (2ª instancia viva de la clase de bug de hoy). Decisión de Bilal:
  SÍ se escribe el test, anotado como **disparador de release** (§2), no ahora. Dejarlo sin test
  sería repetir el error a sabiendas.

---

## 5. Checklist — cómo correr una auditoría (sin re-pensarla)

Un yo futuro (o Claude Code) puede ejecutar esto tal cual:

1. **Re-traza la matriz (§1) DESDE EL CÓDIGO, no de memoria.** Para cada canal, sigue qué fuentes
   sensibles puede leer:
   - Salidas de agente: `agent_dispatch.dispatch_message` (qué verbos, qué exige `trusted`).
   - Inyección de prompt: `grep -rn "system_block\|build_chat_system\|build_system_prompt\|retrieve" app/`.
   - Adaptadores: `a2a_server.py`, `nostr_listener.py`, `mcp_server.py`, `email_connector.py`, `scheduler.py`.
2. **Busca celdas vacías o cambiadas** — un canal nuevo, un dato nuevo, o una celda que pasó de ✅ a ☑️.
3. **Verifica que cada invariante afirmada (§3) sigue con test.** `grep` los comentarios que afirman
   ("solo", "never", "nunca", "only the human", "fail-closed") y confirma que existe un test que lo fija.
   Comentario que afirma sin test = arréglalo (añade test) o bájalo a "no garantizado".
3b. **Corre los tests de seguridad** que existen: `python tests/memory_gate_test.py` (verde). Añade el
    que falte de la lista de §3.
4. **Revisa los tres cubos de §4.** ¿Algún hueco "aceptado" cambió de contexto? ¿Algún "pendiente"
   ya se puede cerrar? ¿Algún "surgido" nuevo?
5. **Actualiza este doc y el threat-model** con lo trazado. La matriz es la fuente de verdad del egreso.

> La matriz de §1 es lo que de verdad importa: rellénala siempre con el estado REAL. Un ✅ sin
> mecanismo+test detrás es la mentira que causó el bug de hoy.
