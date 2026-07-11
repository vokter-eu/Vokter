# Fase 3.2 · Paso 3 — Invertir la precedencia del llavero (PLAN, sin ejecutar)

**Fecha:** 2026-07-11 · **Estado:** propuesta para revisión. NADA de esto se ha
implementado. No se toca el arranque real hasta el OK explícito de Bilal.

**Objetivo:** que Vokter lea la llave de cifrado de la DB **primero del llavero
del sistema (GNOME Keyring)**, y use el **fichero `.db_key` como respaldo**, sin
que nadie pueda quedar nunca fuera de su propia base de datos.

---

## 0. Lo que YA está hecho (el paracaídas)

- Copia de seguridad en `~/vokter-backups/2026-07-11_fase3-llavero/` (fuera del
  repo y de `runtime/`, en solo lectura 0400).
- Verificada con sha256 **y** con una prueba real de descifrado: la copia de
  `.db_key` abre la copia de `vokter.db` (14 tablas reales). No es solo idéntica:
  **se abre**.
- Instrucciones de restauración en `LEEME_RESTAURAR.txt` dentro del backup.
- **Foto del estado real hoy:** el llavero está DISPONIBLE pero el slot
  `vokter/db_key` está **VACÍO** (el espejo de la Fase 2 nunca lo pobló en una
  sesión real). Tu primer arranque real caerá en la **Situación 2 (migración)**.

---

## 1. La secuencia, en lenguaje sencillo

Hoy el orquestador hace: "¿hay fichero? úsalo; si no, acuña uno". Lo vamos a
cambiar por: "pregunta al llavero primero; usa el fichero como red; y **jamás
acuñes una llave nueva si podría haber datos que abrir**".

La regla de oro no cambia: **si el llavero falla → fichero; nunca dejar a nadie
sin su DB, nunca abrir un diálogo del llavero que el usuario no pidió.**

Precedencia = **orden de intento validado**, no elección a ciegas. Es decir:
1. Pide la llave al llavero.
2. **Comprueba que esa llave de verdad abre la DB** (o que coincide con el
   fichero, que ya está probado).
3. Si no abre / no está / no se pudo preguntar → **cae al fichero** (probado).
4. Si tampoco hay una llave usable → **fallo ruidoso**, sin tocar nada. Solo se
   acuña en el único caso en que no hay absolutamente nada que perder.

---

## 2. La tabla de decisión (las 5 situaciones) y el invariante

**Entradas en cada arranque:**
- **Fichero** `.db_key`: *presente y legible* / *presente pero ilegible* / *ausente*.
- **Llavero**: *no disponible* (bloqueado, headless, sin D-Bus, timeout) /
  *disponible y vacío* / *disponible con llave*.
- **DB** `vokter.db`: *presente* / *ausente*.

**Invariante innegociable:** se acuña una llave nueva en **exactamente una
casilla** (Situación 5). En cualquier otra, si no hay una llave usable →
**fallo ruidoso, nunca acuñar**. En particular: "no pude preguntar al llavero"
≠ "el llavero está vacío", y "no pude leer el fichero" ≠ "no hay fichero".

| # | Situación | Llavero | Fichero | DB | Acción | ¿Acuña? |
|---|-----------|---------|---------|----|--------|---------|
| **1** | Estado estable (post-migración) | con llave (= fichero) | presente, igual | sí | **Usar la del LLAVERO**; fichero de respaldo | No |
| **2** | **Migración (tu caso HOY)** | disponible, vacío | presente, legible | sí | **Usar la del FICHERO** y sembrar el llavero | **No** |
| **3** | Llavero caído/bloqueado | no disponible | presente, legible | sí | **Caer al FICHERO** (probado); reintentar sembrar otro día | **No** |
| **4a** | Discrepancia | con llave ≠ fichero | presente | sí | Preferir la que **ABRE la DB**; por defecto el fichero (probado) + **AVISO fuerte** | No |
| **4b** | Llavero sin fichero | con llave | ausente | sí | **Validar que abre la DB**; si abre → usarla y **re-crear el fichero**; si no → fallo ruidoso | No |
| **4c** | Fichero ILEGIBLE | con llave que **abre la DB** (validado) | presente, ilegible | sí | **Usar la llave del LLAVERO** solo si el validador confirma que abre (+ recrear fichero); si no abre / no hay / no disponible → **fallo ruidoso** | **No** |
| **5** | Primer arranque real | **probado** disponible y vacío | ausente | **no** | **Acuñar** llave nueva → fichero + sembrar llavero | **Sí (única)** |

Nota sobre la 5: si el fichero y la DB están ausentes **pero el llavero no se
pudo preguntar**, NO se acuña todavía — se falla/reintenta, porque el llavero
podría guardar una llave que no pudimos leer.

**Cómo se implementa el invariante:** la función `ensure_db_key()` se reescribe
como una decisión con **denegar por defecto**: `mint` solo se alcanza tras pasar
un único `if` positivo (llavero **probado** disponible y vacío **y** sin fichero
**y** sin DB). Se añade la comprobación de existencia de la DB (hoy no la mira) y
una lectura del fichero que **distingue "no existe" de "existe pero no se pudo
leer"** (esta última NO se trata como "ausente": se intenta la llave del llavero
**validándola contra la DB**, y solo si no abre → fallo ruidoso — decisión de
Bilal 2026-07-11, ver §7).

---

## 3. Quién valida "esta llave abre la DB", y cuándo (punto delicado)

**Restricción real del entorno (comprobada):** ningún intérprete tiene a la vez
las dos piezas. El python del **orquestador** (sistema) tiene `secretstorage`
(llavero) pero **no** `sqlcipher3`. El **venv / binario congelado** tiene
`sqlcipher3` pero **no** `secretstorage`. Por tanto el orquestador no puede leer
el llavero *y* abrir la DB en el mismo proceso.

**Solución propuesta:** un **validador aparte** que abra la DB en solo-lectura e
inmutable con una llave candidata y devuelva 0 si descifra, ≠0 si no. Vive donde
está `sqlcipher3`:
- en dev: el `runtime/venv/bin/python` (ya se usa así en los tests);
- en máquina de usuario: **el propio backend congelado**, con un modo
  `--verify-key` (lee `VOKTER_DB_KEY`, intenta abrir, sale 0/≠0). Es la única
  pieza con `sqlcipher3` en esa máquina.

El orquestador (que sí lee el llavero) **llama a ese validador** para cada llave
candidata **antes** del lanzamiento real. Fallback síncrono y limpio; si el
validador no puede ni ejecutarse, se trata como "no pude validar" → **caer al
fichero probado, nunca acuñar**.

**Atajo seguro para el caso común:** en las Situaciones 1/2/3 se puede decidir
comparando la llave del llavero con la del **fichero** (que ya está probado) sin
abrir la DB. El validador-que-abre solo es imprescindible en 4a/4b (discrepancia
o llavero sin fichero).

**Interruptor de emergencia:** `VOKTER_KEY_SOURCE=file` (mismo patrón que los
`VOKTER_DESKTOP_*`) fuerza modo fichero-solo, saltándose el llavero. Revierte al
instante, sin recompilar.

---

## 4. Puesta en escena: cuándo pasamos de material desechable a tu Vokter real

- **Etapa 0 — HECHA:** backup + paracaídas verificado (descifra de verdad).
- **Etapa 1 — código, sin cambiar comportamiento:** implementar la tabla + el
  validador **detrás del interruptor**, con el **valor por defecto todavía
  fichero-primero** (cero cambios en tu arranque). Tests unitarios sobre
  material **desechable** para las 5 situaciones → **verde en todas**.
- **Etapa 2 — ENSAYO en solo lectura contra tus datos reales:** ejecutar la nueva
  lógica de selección contra tu `.db_key` / `vokter.db` / llavero reales, que
  **imprima qué llave elegiría** y confirme que **abre tu DB real**, SIN
  cablearla al arranque. Es el momento exacto en que "tocamos" tu Vokter real, y
  es **no destructivo** (solo lectura).
- **Etapa 3 — activar (primer cambio real de arranque):** solo si Etapa 1 verde
  **y** paracaídas verificado **y** Etapa 2 verde **y** interruptor listo, se
  invierte el defecto a llavero-primero. Entonces sí, las pruebas de logout y
  reinicio de abajo.

**Antes del salto de Etapa 2 a Etapa 3 se verifica:** las 5 casillas verdes en
desechable, el ensayo real elige una llave que abre tu DB, el interruptor
`VOKTER_KEY_SOURCE=file` funciona, y el backup sigue intacto (sha256).

---

## 5. Pruebas de logout/login y reinicio real — y qué deberías ver

| Prueba | Qué hago | Qué deberías ver TÚ |
|--------|----------|---------------------|
| **A — arranque normal** (sesión desbloqueada) | Arrancar Vokter dos veces | 1ª vez: log "uso la llave del fichero, siembro el llavero" (Situación 2). 2ª vez: "uso la llave del LLAVERO, fichero de respaldo en sync" (Situación 1). Chat y datos intactos. |
| **B — logout / login** | Cerrar sesión, volver a entrar, arrancar Vokter | Vokter arranca tras el login. El log muestra que abre con la llave (llavero; o fichero si en ese instante el llavero seguía bloqueado). Datos intactos. |
| **C — reinicio REAL** | Reiniciar el equipo, arrancar Vokter enseguida | Vokter **arranca igual** aunque el llavero tarde o esté bloqueado (Situación 3 → fichero). Log: caída limpia al fichero. Datos intactos. **Esta es la prueba de que nadie queda fuera de su DB.** |
| **D — interruptor** | `VOKTER_KEY_SOURCE=file` | Arranca ignorando el llavero. Datos intactos. |

---

## 6. Qué puede salir mal en cada punto y cómo lo detectamos A TIEMPO

- **Confundir "no pude preguntar al llavero" con "vacío"** → acuñaría y te dejaría
  fuera. *Mitigación:* `is_available()` ya prueba disponibilidad **positivamente**;
  `mint` está encerrado en la Situación 5 con llavero **probado** disponible y
  vacío. *Se detecta* en los tests unitarios casilla por casilla (Etapa 1).
- **Llave del llavero distinta/corrupta** → no abriría la DB. *Mitigación:* el
  validador abre-para-probar antes de lanzar; si no abre, cae al fichero probado.
  *Se detecta* en el ensayo de Etapa 2 y en los tests 4a/4b.
- **Fichero ilegible (permisos/corrupción)** → tratado como *no acuñar / fallo
  ruidoso*, nunca como "ausente". *Se detecta* con una lectura que distingue
  error de ausencia.
- **El validador no puede ejecutarse** (entorno sin `sqlcipher3`) → se trata como
  "no pude validar" → fichero probado, nunca acuñar. *Se detecta* en Etapa 2.
- **Sembrar el llavero escribe en tu slot real** → por eso la foto del estado
  está en el paracaídas, con el comando para vaciarlo si quieres volver atrás.
- **Regresión silenciosa** → el defecto sigue en fichero-primero hasta Etapa 3;
  el interruptor revierte sin recompilar.

---

## 7. Estado y decisiones fijadas para la Etapa 3 (2026-07-11)

**Etapas 1 y 2 HECHAS y revisadas por Bilal (verdes, solo lectura).** Working
tree en `feat/desktop-app`:
- `desktop/keysource.py` — decisión pura + validador `key_opens_db`.
- `desktop/keysource_test.py` — 5 situaciones + sub-casos + negativos + interruptor, TODO VERDE.
- `desktop/keysource_dryrun.py` — ensayo solo-lectura; caso real = Situación 2, la llave del fichero abre la DB, slot del llavero intacto.
- `desktop/keychain.py` — `is_reachable_readonly()` (alcance sin sonda).
- `desktop/freeze/vokter_backend.py` — modo `--verify-key` (en el fuente).
- `orchestrator.py` **SIN tocar** — el arranque real NO ha cambiado.

**Tareas previas ADELANTADAS (2026-07-11, aprobadas por Bilal; SIN invertir la
precedencia ni tocar el arranque real):**

1. ✅ **HECHO — Binario congelado reconstruido con `--verify-key` + validador
   endurecido.** El nuevo `freeze/dist/` incluye el modo `--verify-key` (imprime
   un MARCADOR de capacidad y sale 0/≠0 sin arrancar servidor). `key_opens_db`
   endurecido: (a) confía en el veredicto SOLO si el subproceso imprimió el
   marcador — un binario viejo que no entiende la bandera nunca lo imprime → se
   trata como "no fiable" → False; (b) lo lanza en su propia sesión y lo mata por
   grupo (`killpg`) si se cuelga → nunca deja un servidor fantasma; (c) sandbox
   defensivo (`VOKTER_DB`→temp, `VOKTER_BIND`→dirección no vinculable RFC-5737)
   para que un binario viejo falle rápido sin tocar datos ni ocupar puerto.
   Verificado: nuevo binario abre/​rechaza correcto/incorrecto (incl. la DB REAL,
   solo lectura), 0 servidores; tests Part 3 (binario sin marcador → no fiado;
   binario colgado → muerto por timeout).

2. ✅ **HECHO — Política 4c resistente implementada** en `decide()` + tests: si el
   fichero es ilegible **pero** el llavero tiene una llave que —**validada con el
   validador**— ABRE la DB, se usa esa llave (+ recrear fichero + AVISO); **solo
   si se comprueba que abre, nunca a ciegas**; si no abre / no hay llave / no hay
   DB que validar → **fallo ruidoso**, nunca acuñar. (Cambia SOLO la lógica de
   `decide()`, que NO está cableada al arranque; el arranque real no cambia.)

**Lo que QUEDA para la Etapa 3 (hacer FRESCO; repasar este plan y OK explícito
ANTES de tocar nada):**

3. **Invertir el defecto** a keychain-first + cablear `decide()` en
   `ensure_db_key()` (orchestrator.py:99), conservando el interruptor
   `VOKTER_KEY_SOURCE=file`. ← ESTE es el primer cambio real del arranque.

4. **Pruebas reales:** logout/login y REINICIO (tabla de la §5), con el
   interruptor y el paracaídas listos.

**Notas para el cableado (Etapa 3):**
- **`recreate_file` es best-effort, NUNCA una condición de arranque.** En 4b/4c,
  si la llave del llavero (validada) abre la DB, el arranque DEBE tener éxito con
  esa llave **aunque re-escribir el fichero falle** (p.ej. fichero ilegible por
  permisos → probablemente tampoco re-escribible). Recrear el respaldo se intenta
  y se loguea; si falla, se sigue. Lo contrario reintroduciría justo el bloqueo
  que el 4c evita.
- **El binario capaz vive en `freeze/dist/` (git-ignored).** La capacidad
  `--verify-key` está en el fuente commiteado, pero un checkout nuevo o una
  máquina que no haya reconstruido tiene un binario INCAPAZ → la rama congelada
  de `key_opens_db` degrada a False ahí hasta reconstruir. Correcto para release
  (se compila por-OS), pero **reconstruir el congelado es un paso de release**.

---

## Qué NO se hace hasta tu OK

No se invierte la precedencia, no se toca `ensure_db_key()` en el arranque real,
no se cambia el defecto. Este documento es solo el plan. Con tu OK empiezo por la
**Etapa 1** (código detrás del interruptor + tests en desechable), y te paro
antes de la Etapa 3.
