# Vokter — Memoria personal (estado + deuda mapeada)

Documento vivo del frente de memoria personal (los "hechos que Vokter recuerda de ti").
Origen de la deuda de contradicción: **jcode** (github.com/1jehuang/jcode, harness de
codificación en Rust) — su "consolidation runs in the background to check for stale or
conflicting facts" nos hizo ver que otro proyecto llegó al MISMO problema de forma independiente
(diferencia clave: jcode absorbe en silencio; Vokter eligió explícito+confirmación a propósito —
en un asistente que puede guardar historial médico, la memoria silenciosa es justo lo que
rechazamos). Diagnóstico completo hecho 2026-08-04 sobre el código y las DBs reales (lectura
read-only + immutable, sin tocarlas).

## Cómo funciona HOY

- **Almacenamiento (`app/db.py` tabla `memory`, `app/memory.py`):** tabla PLANA, cada hecho una
  fila suelta (`content` VERBATIM, `source` told|learned, `created_at`, `embedding` NULL,
  `confidence` solo-display). Sin categorías, sin claves, sin tipos semánticos. `add()` es un
  INSERT puro: **cero detección de duplicados / solapes / contradicciones**. Se apila sin más.
- **Inyección en el prompt (`memory.system_block`, `app/chat.py`):** se inyectan **TODOS los
  hechos, siempre el conjunto entero** — sin top-k, sin límite, sin recuperación por relevancia.
  Gateado solo a la sesión humana local (P2). Chat corre `llama3.2:3b` con `context_size=8192`,
  `max_history=20`, `rag_chunks=4`.
- **Volumen real hoy:** memoria viva (app instalada, `~/.local/share/vokter/`) = **0 hechos**.
  La DB de dev del repo es de esquema anterior a la feature (sin tabla `memory`). → Toda la deuda
  de abajo es **anticipada**, no un incendio.

- **Principio innegociable (base de todo el frente):** *detectar sí, decidir no.* Nada entra sin
  acción explícita del usuario (`add()`); la Fase 2 solo PROPONE (`extract_candidate` jamás
  guarda); borrar es siempre explícito. **Ninguna consolidación futura puede resolver un choque
  en silencio** — Vokter puede señalar la contradicción, el usuario decide cuál es verdad.

## Deuda mapeada — DOS frentes INDEPENDIENTES

El diagnóstico destapó dos problemas que parecían uno. **No se resuelven con la misma pieza.**
Comparten un substrato (la columna `embedding`), pero cada uno necesita su mecanismo.

### 1. Inflado del prompt (problema GEMELO, el nuevo)

- **Qué:** como se inyecta TODO, el `system_block` crece sin tope con el nº de hechos y compite
  por el presupuesto de 8192 tokens contra el historial (20 turnos) y el RAG (4 chunks).
- **Umbral (aprox., ~15-25 tokens/hecho para hechos cortos en español):**
  - **~20 hechos (~400 tok):** irrelevante.
  - **~100 hechos (~2000 tok):** **se nota** — ~25% de la ventana solo en memoria, empieza a
    desplazar historial/RAG **y** añade latencia de procesado de prompt en CADA turno.
  - **~200 hechos (~4000 tok):** **problema real** — media ventana consumida antes de historial y
    RAG (degrada CALIDAD, no solo velocidad) + segundos de time-to-first-token por turno.
  - **Matiz CPU:** en el ThinkPad sin GPU el reprocesado del system prompt cada turno es lo caro
    → el umbral práctico llega ANTES que en una app de nube. La velocidad muerde primero.
- **Solución prevista = retrieval por embeddings** (la columna `embedding` NULL, comentada como
  "reserved (Phase 3 retrieval)"). **La maquinaria ya existe:** `app/rag.py` (`embed()` +
  `cosine()` + `retrieve(top_k)`) sobre el mismo patrón `embedding TEXT` que la tabla `chunks`.
  Traer solo los k hechos relevantes al mensaje → **prompt acotado sea cual sea el tamaño**.
- **Cubre:** SOLO el inflado.
- **Disparador:** *cuando la memoria pase de **~100 hechos** (o antes si se nota latencia en el
  ThinkPad).* Por debajo de ~30-50 no merece la pena.

### 2. Contradicción entre hechos (deuda original de Fase 3 / jcode)

- **Qué:** append-only sin detección → "vivo en Formentera" y "me mudé a Barcelona" coexisten, y
  ambos acaban en el prompt = contradicción. Empeora con el tamaño.
- **El retrieval por embeddings NO lo resuelve (confirmado):** ambos hechos son **igual de
  relevantes** a "¿dónde vivo?" → el top-k **trae los dos** igual. Es más, un buen retriever
  puede **concentrar** el choque (los pone lado a lado por ser los más on-topic). El único caso
  en que "esconde" un choque es por suerte/ruido (el hecho viejo cae bajo el corte k) — y eso es
  justo el **borrado silencioso** que el principio prohíbe.
- **Embeddings como substrato COMPARTIDO, uso distinto:** el retrieval usa embeddings en
  *query-time* (top-k relevantes); la detección de contradicción los usaría en *write-time*
  (coseno del hecho nuevo contra los existentes para marcar "esto es del mismo tema que #X").
  Mismo ingrediente, mecanismo distinto. Por eso son frentes independientes aunque toquen la
  misma columna.
- **Cubre:** su propia pieza (detección de choques + resolución **decidida por el usuario**),
  ortogonal al inflado.
- **Disparador:** *cuando haya memoria REAL con choques observables, no antes — diseñar sobre
  datos reales, no sintéticos.* Con 0 hechos hoy, diseñarlo sería especular sobre un problema que
  no existe y no se podría validar sin inventar contradicciones falsas. Esperar a ver cómo se dan
  los choques de verdad.

## Resumen

| Frente | Lo resuelve | Disparador |
|---|---|---|
| Inflado del prompt | **Retrieval por embeddings** (reusa `rag.py`; columna `embedding`) | memoria > ~100 hechos |
| Contradicción de hechos | Pieza propia: **detectar** (coseno write-time) + **resolución del usuario** (nunca silenciosa) | haya memoria real con choques observables |

Los dos comparten el gancho `embedding`, pero **el retrieval solo cubre el inflado**; la
contradicción necesita su propia capa de detección+resolución. Ninguno se construye ahora.
