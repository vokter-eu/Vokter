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
| **Documentos**      | ✅ solo trusted | ☑️ solo trusted | por diseño (host)| —              | local (no sale)| — (es entrada) | 🟡 §8.7             |
| **Conversaciones**  | ☑️ por peer †   | ☑️ por peer     | 🔶 sin ownership | —              | local          | —              | 🟡 §8.7 + 🔶        |
| **Llaves** (DB/priv)| 🔒             | 🔒             | 🔒              | 🔒 (§)         | 🔒             | 🔒             | 🔒                 |
| **Tokens** (admin/A2A/humano)| 🔒    | 🔒             | 🔒              | 🔒             | 🔒             | 🔒             | 🔒                 |

> **† Conversaciones/A2A — otro eje, hallazgo ABIERTO.** El ☑️ mide EGRESO del dato del humano, y ahí
> está cerrado (C2a: `human_owned`). Pero hay un eje distinto que la matriz NO mide —
> **integridad/autorización par↔par**: el aliasing de `contextId` y el secuestro de la sesión de
> negociación (la invariante "solo quien abrió avanza" está rota para A2A). **Ver §4 · C2b y §3 ·
> invariante #6.** La celda en verde NO significa "aquí no hay nada".

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
  remitente firmado en allowlist o rating `trusted`. **Test: ✅ `tests/a2a_trust_boundary_test.py`
  (C1)** fija la frontera del dispatcher (no-trusted → solo tarjeta pública, cero HTTP privado;
  trusted → ask/browse/wallet_balance/plan/negotiate pero NO wallet_send; dispatcher sin token
  humano) + doble tripwire (conductual + de fuente). NOTA: cubre el gate COMPARTIDO de
  `dispatch_message` (A2A y Nostr) y el `_is_trusted` de A2A; la *computación* de trust
  específica de Nostr (`_inbound_trusted`: allowlist/rating) aún no tiene test propio.
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
4. **Pasar de 1 a 2+ pares A2A de confianza** (emitir/compartir credencial A2A con una SEGUNDA
   entidad distinta). Ese evento EXACTO es cuando C2b (§4) despierta: el aliasing de `contextId` y el
   secuestro de la sesión de negociación dejan de ser teóricos. Antes de añadir el 2º par: o tokens
   por par (fix real, §4·C2b·4), o la invariante 🔴 de config (§4·C2b·1) verificada explícitamente.
   **No se añade un 2º par sin una de las dos.**

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
| 2 | **Frontera de confianza: caller no-trusted → solo tarjeta pública; verbos privados exigen `trusted=True` (fail-closed)** | `agent_dispatch.dispatch_message` | ✅ **`tests/a2a_trust_boundary_test.py` (C1)** — doble tripwire (spy sobre `_http` + posición del gate en fuente); disparador de release BLOQUEANTE. Cerrada la 2ª instancia de la clase de bug de hoy. |
| 3 | `system_block` tiene un único punto de inyección (chat.py) | arquitectura (grep confirma 1 llamante) | ❌ sin test-guardia contra un llamante nuevo |
| 4 | Llaves privadas y tokens nunca egresan | serialización (nunca se devuelven) | ❌ sin test (negativo; factible como test-grep de responses) |
| 5 | Browse no alcanza hosts privados (SSRF) | `_is_private_host`/`_is_allowed` | ❌ no se encontró test |
| 6 | **Negociación: "solo quien abrió una sesión puede avanzarla" (`s.peer != peer`)** | `negotiation.handle_inbound` — VERDAD para Nostr (`peer`=pubkey descifrada del DM), **FALSA para A2A** (`peer`=`contextId` que el llamante elige sin autenticar) | ❌ sin test; y para A2A la afirmación **no se cumple** hasta que haya identidad-por-par (§4 · C2b · punto 2). Misma clase que el bug de hoy: invariante afirmada que un canal no impone. |

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

- **C2 · `conversation_id` sin comprobación de propiedad — ANALIZADO 2026-07-31 (ver
  `/home/harry/vokter-C2-analysis.md`), partido en DOS.** Respuestas a las tres preguntas, desde el
  código:
  - (a) **ENUMERAR: NO.** No existe endpoint que liste conversaciones; los dos únicos `SELECT`
    sobre `conversations` filtran por `conv_id` concreto. Solo se lee un id ya conocido.
  - (b) **FILTRACIÓN del conv_id del humano: NINGUNA hoy.** `/api/ask` lo devuelve, pero dispatch
    A2A/Nostr devuelve solo `answer` y MCP `ask` solo el texto (el conv_id es *entrada* del host,
    nunca *salida*); no se loguea, no está en la card; UUIDv4; el humano ni pasa por dispatch.
  - (c) **COSTE: barato** para el hilo del humano (no el modelo caro que se temía).

  - **C2a · confidencialidad del hilo del HUMANO → DECIDIDO: bit-guard (Bilal 2026-07-31), pendiente
    de build.** El vector es TEÓRICO hoy, pero la seguridad es *por-no-filtración* (a un commit
    despistado de volverse viva EN SILENCIO — misma clase que el bug A2A que abrió este frente). Fix
    barato y por-construcción: columna `human_owned` estampada al crear la fila con el `human` que ya
    calcula `/api/ask`; los lectores (`_load_history` y `/api/memory/suggest`→`_recent_user_context`)
    solo sirven filas de su propia clase de propiedad → un caller sin marca humana no ve filas
    human_owned (deny-closed, sin oráculo existe/no-existe). + test-invariante estilo tripwire C3. NO
    construido aún.
  - **C2b · aliasing de `contextId` entre pares A2A → ÍTEM DE AUDITORÍA PROPIO. ANALIZADO 2026-08-01
    (Opus 4.8, trazado desde código): DOCUMENTAR Y APLAZAR — el vector está DORMIDO, no muerto.** No
    toca datos del humano (C2a lo blindó: A2A entra `human=False` → `human_owned=0`). Cuatro puntos,
    ninguno menor:

    1. **🔴 INVARIANTE DE CONFIG (REQUISITO ACTIVO, no nota pasiva) — es lo ÚNICO que mantiene C2b
       dormido HOY:** *un token A2A compartido = UNA sola identidad de confianza; NUNCA entregar el
       mismo `A2A_TOKEN` a dos entidades distintas.* Compartir un bearer entre dos agentes es, por
       definición, declararlos la misma identidad; el aliasing es la CONSECUENCIA de romper esta regla,
       no una vía independiente. **Si algún día se documenta "cómo añadir un par A2A", esta línea va
       ahí, en rojo.**

    2. **HALLAZGO DE FONDO (no puede perderse) — la invariante de negociación YA ESTÁ ROTA para A2A.**
       `negotiation.py` afirma "solo quien abrió una sesión puede avanzarla" (`s.peer != peer`,
       `negotiation.py:139`) y cree atar la sesión a un *par autenticado* (`peer` en su docstring:
       "Nostr sender pubkey / A2A context"). Pero por A2A ese `peer` es la `contextId` que el llamante
       ELIGE sin autenticar (`agent_dispatch.py:107` `handle_inbound(context_key, …)`, y `context_key`
       = `contextId` de `a2a_server.py:122`). Para Nostr la afirmación es VERDAD (`sender_hex` = pubkey
       descifrada del DM NIP-17, `nostr_listener.py:175`); para A2A es FALSA. Es la MISMA clase que el
       bug A2A original y que la celda Documentos/A2A: una invariante AFIRMADA que un canal no cumple.
       **Ligado a §3 "afirmar⇒testear": hoy esa afirmación de negociación NO tiene test que la imponga
       para A2A → registrada como invariante #6.**

    3. **EL VECTOR con su gravedad real (documentos + DINERO, no metadatos de agente).** Con 2+ pares
       distintos compartiendo token, par B repite la `contextId` de A y hereda su estado en DOS
       consumidores de la misma clave:
       - **Lectura** del hilo `human_owned=0` de A — que puede contener **fragmentos de MIS documentos**
         (el RAG `retrieve` en `chat.py` NO está gateado por `human`; un par trusted recibe respuestas
         fundamentadas en mis docs). B ve QUÉ preguntó A y qué se le respondió.
       - **Escritura/inyección** en el hilo compartido → envenena lo que A ve en turnos futuros.
       - **Secuestro de la sesión de NEGOCIACIÓN — toca DINERO:** B avanza/acepta/espía los términos del
         trato de A (`handle_inbound`). Acotado (suelo secreto, tope por par), pero es el daño más activo.

    4. **FIX REAL cuando proceda (NO ahora):** token A2A DISTINTO por par → `context_key` cuelga de la
       identidad del token → aliasing estructuralmente imposible Y el `peer` de negociación pasa a ser
       honesto. **Namespacing** (prefijar `context_key` con la identidad del token presentado) = gancho
       INERTE opcional: hoy es no-op con un token único, deja el código correcto-por-construcción para
       la migración — pero **hereda** la infalsificabilidad del token futuro, NO la crea (no es garantía
       criptográfica). **Disparador del fix: "cuando exista un caso real de 2+ pares A2A"** (ver §2,
       disparador 4). NO construir; decidir junto con el modelo de identidad A2A.

- **C1 · Frontera de confianza (invariante #2) — CERRADO 2026-07-31.** El hueco que la matriz
  cazó (2ª instancia de la clase de bug de hoy) ya tiene test: `tests/a2a_trust_boundary_test.py`,
  con doble tripwire (conductual: spy sobre `_http` falla si un no-trusted provoca una llamada de
  backend; de fuente: el gate `if not trusted: return` debe preceder a todo handler `if tool ==`).
  **Enganchado al disparador pre-release en BLOQUEANTE** (no aviso): `predist` = `test:csp &&
  probe:csp && test:a2a`, los TRES tests causales bloquean release si fallan. Un aviso sobre una
  invariante de seguridad-frontera era teatro; bloqueante es lo coherente con "afirmar⇒testear".

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
