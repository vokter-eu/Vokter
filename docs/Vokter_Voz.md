# Diagnóstico — La Voz de Vokter (TTS + STT) y el Multilingüe Europeo

> Estado: DIAGNÓSTICO. No se ha tocado código. Este documento fija el **estado real**
> del subsistema de voz Y del soporte multilingüe (tres capas), trazado desde el
> código y medido/probado en esta máquina, antes de decidir nada. Espera el OK de
> Bilal para evaluar soluciones.
> Fecha: 2026-08-03 · Rama: `feat/desktop-app` @ `9b4b6cc` · Alcance: `app/voice/`,
> `app/agent_config.py`, UI, empaquetado, modelo de chat.
>
> §0-6: la voz (TTS/STT) aislada. **§7: el reencuadre — multilingüe europeo como
> característica, que toca TRES capas (entender→pensar→hablar) + la selección.**

---

## 0. Resumen para decidir (TL;DR)

- **✅ RESUELTO (2026-08-03):** el frente se cierra **barato, sin cambiar de motor**. Bilal
  probó por oído: Piper *medium* le vale (no era robótico — era que **hablaba inglés por no
  tener voz española**), `high` no se gana su peso, y en español elige **acento de España**.
  → **Decisión: seguir con Piper, tier `medium`, una voz por idioma.** El único trabajo real
  que queda es **cablear** (unificar el selector de idioma → 3 capas). Ver §5 (veredicto) y §7.
- **La voz de producción es INGLESA.** `en_US-lessac-medium` (VITS/Piper, calidad
  *medium*). Es lo que va sembrado en el `.deb` y lo que arranca por defecto. **No hay
  ninguna voz española configurada en el repositorio** — ni en `config.py`, ni en el
  orquestador, ni en ningún `.env`/script. El `es` que aparece en la UI es el idioma
  de la **respuesta escrita** del chat, no de la voz.
- **El pipeline y la configuración están descartados como causa** de que suene robótica:
  Piper genera un WAV a 22 kHz que se reproduce tal cual (sin recomprimir), con los
  parámetros de inferencia de fábrica. Si suena robótica, es por el **modelo/voz** o por
  **Piper (VITS) de suyo** — y eso solo lo zanja el oído (ver §5, prueba de escucha).
- **Peso y velocidad NO son el problema.** Piper *medium* sintetiza una frase de ~4 s
  en **0,32 s** en CPU (≈14× más rápido que tiempo real). *high* pesa el doble y tarda
  ~1,8 s (≈2× tiempo real): más lento pero viable.
- **El acoplamiento es limpio.** Todo el vínculo con el motor son **2 endpoints HTTP**
  (`texto→WAV`, `audio→texto`). La UI nunca toca Piper. Cambiar de **voz** = trivial;
  cambiar de **motor** = un adaptador contenido al módulo de backend + 2 líneas de
  empaquetado. La UI no se toca en ningún caso.

---

## 1. Qué hay hoy, exacto

| Pieza | Motor | Modelo concreto | Peso | Local |
|---|---|---|---|---|
| **TTS (hablar)** | Piper (red neuronal VITS) | `en_US-lessac-medium`, 22.050 Hz, 1 hablante | 63 MB (.onnx) | Sí — `use_cuda=False`, CPU, sembrado offline |
| **STT (escuchar)** | faster-whisper (ctranslate2) | `base`, cuantizado `int8` | 139 MB | Sí — CPU, sembrado offline |

Fuentes en el código:
- `app/config.py:69-71` — valores por defecto `WHISPER_MODEL=base`, `WHISPER_DEVICE=cpu`,
  `PIPER_VOICE=en_US-lessac-medium` (todos sobreescribibles por env, ninguno sobreescrito).
- `app/voice/piper.py` — endpoint `POST /api/voice/speak` (texto→WAV), descarga la voz de
  HuggingFace la primera vez si no está sembrada, la carga una vez (`_voice_lock`), síntesis
  síncrona en el threadpool de FastAPI.
- `app/voice/whisper.py` — endpoint `POST /api/voice/transcribe` (audio→texto), prefiere el
  modelo sembrado `base-int8`, si no lo descarga.
- `desktop/orchestrator.py:520 seed_voice()` — copia los modelos sembrados a `DATA_DIR/models`
  en el primer arranque (offline out-of-the-box). Los `jobs` confirman los modelos: piper
  `en_US-lessac-medium.onnx`, whisper `base-int8`.
- `desktop/runtime/voice-seed/` — los ficheros reales que viajan en el paquete: piper 61 MB
  (inglés), whisper 142 MB.
- `desktop/freeze/vokter_backend.spec:23-28` — `collect_all` de piper, ctranslate2,
  faster_whisper, onnxruntime, av (native pieces + espeak-ng-data viajan juntos).

## 2. Por qué suena robótica — contra las 4 hipótesis

Separo lo verificable por evidencia de lo que necesita oído (no he escuchado el audio).

1. **Pipeline** → **descartado.** `synthesize_wav` → WAV 22 kHz → `Audio.play()` en el
   navegador (`app/static/app.js:91-104`). Sin resampleo, sin códec con pérdida, sin
   post-proceso. Nada degrada la señal por el camino.
2. **Configuración** → **descartada.** 22.050 Hz, parámetros de inferencia por defecto de
   Piper (`noise_scale 0.667`, `length_scale 1`, `noise_w 0.8`). No hay nada mal ajustado.
3. **Modelo / calidad** → posible. Piper tiene 4 niveles: `x_low`, `low`, **`medium`**,
   `high`. Usamos *medium*. Existe *high* para lessac. lessac es una voz inglesa estándar
   y limpia, no una mala elección.
4. **Motor de suyo** → posible. Piper (VITS, ~2021) es claro pero arrastra el timbre "de
   TTS"; no suena tan natural como motores neuronales más nuevos.

**Interpretación:** si lo que Bilal oye es la voz **inglesa** lessac-medium y aun así le
resulta robótica, el peso cae más en "Piper/VITS de suyo" que en "elegimos una voz mala".
Pero **esa conclusión no se traslada al español**: la calidad de las voces españolas de
Piper es distinta y variable. Por eso el discriminador real es una **prueba de escucha en
español** (§5).

## 3. Coste de cambiar — dónde cae

La frontera con el motor son **2 endpoints HTTP** y nada más:
- `POST /api/voice/speak` — recibe `{text}`, devuelve `audio/wav`.
- `POST /api/voice/transcribe` — recibe audio multipart, devuelve `{text}`.

La UI (`app/static/app.js:94,133`) solo conoce esas dos URLs; no sabe qué motor hay detrás.

- **Cambiar de voz** (mismo Piper, otro `.onnx`): **trivial.** Cambiar el valor por defecto
  `PIPER_VOICE` + intercambiar el fichero sembrado (`voice-seed/piper/` + el `job` de
  `seed_voice`). Es "cambiar el modelo" → lado barato.
- **Cambiar de motor** (Piper → otro): **contenido.** Reescribir el interior de
  `app/voice/piper.py` manteniendo el contrato `texto→WAV`; cambiar la dependencia
  (`app/requirements.txt`), el modelo sembrado, y dos líneas de empaquetado
  (`vokter_backend.spec` `collect_all` + `package.json` `extraResources`). **La UI intacta.**
  Es "un adaptador nuevo" — la lección del swap de motor — pero pequeño y localizado, no
  cableado por todo el código.

## 4. Restricción CPU y peso — medido en el ThinkPad

Medido con el venv real (`desktop/runtime/venv`), CPU pura, `use_cuda=False`:

| Voz | Tier | Síntesis (frase ~4 s) | RTF | Peso .onnx |
|---|---|---|---|---|
| en_US-lessac-medium (actual) | medium | 0,32 s | 0,07 (14× RT) | 63 MB |
| en_US-lessac-high | high | 1,82 s | 0,44 (2,3× RT) | 113 MB |
| es_ES-davefx-medium | medium | 0,32 s | ~0,08 | 63 MB |
| es_AR-daniela-high | high | 1,66 s | 0,46 (2,2× RT) | 114 MB |

Carga del modelo: ~1,45 s una sola vez. **Conclusión:** velocidad y peso sobran de margen.
*high* es más lento y pesa el doble, pero sigue por debajo de tiempo real → viable en CPU.
Cualquier candidato futuro hereda este listón: correr en CPU sin disparar el peso del `.deb`
(hoy 388 MB).

## 5. Prueba de escucha (pendiente del veredicto de Bilal)

Voces españolas reales disponibles en Piper (v1.0.0):

| Voz | Acento | Calidad | Notas |
|---|---|---|---|
| `es_ES-davefx-medium` | 🇪🇸 España | medium | masculina |
| `es_ES-sharvard-medium` | 🇪🇸 España | medium | 2 hablantes (m/f) |
| `es_AR-daniela-high` | 🇦🇷 Argentina | **high** | femenina |
| `es_MX-claude-high` | 🇲🇽 México | **high** | — |
| `es_ES-carlfm-x_low`, `es_ES-mls_*-low` | 🇪🇸 | bajas | descartadas |

**Restricción de acento:** en acento de **España** la calidad tope es *medium*. La calidad
*high* solo existe en acentos **argentino** o **mexicano**.

4 WAV generados para juicio del oído (misma frase; voces inglesas en inglés, españolas en
español), en `/home/harry/vokter-voz-test/`:
1. `1_en_lessac-MEDIUM_actual.wav` — referencia de hoy.
2. `2_es_ES_davefx-MEDIUM.wav` — mejor voz de España (medium).
3. `3_en_lessac-HIGH.wav` — misma voz que #1 pero *high* (aísla medium→high).
4. `4_es_AR_daniela-HIGH.wav` — mejor calidad en español (high, femenina, acento AR).

Ejes de comparación: **#1 vs #3** = ¿importa el salto de calidad?; **#1 vs #2/#4** = ¿es
Piper de suyo?; **#2 vs #4** = acento (España/medium) vs calidad (LatAm/high).

**✅ VEREDICTO DE BILAL (2026-08-03, prueba de oídos sobre los 4 WAV):**

1. **Español → acento de ESPAÑA** (`es_ES-davefx-medium`, #2). **LatAm descartado de momento**
   (nada de es_AR/es_MX aunque sean *high*).
2. **`high` NO se gana su peso.** #3 (lessac-high) no aporta lo suficiente frente a #1 para
   justificar el doble de tamaño/tiempo → **tier `medium` para todo el multilingüe.**
3. **La voz actual (#1, lessac-medium inglés) es ACEPTABLE**, no robótica.

**Consecuencias — el frente se resuelve BARATO:**
- **NO hay cambio de motor.** Piper *medium* le vale al oído de Bilal → se queda Piper. Los
  candidatos Kokoro/StyleTTS2/XTTS **no se evalúan** (frente cerrado).
- **El "problema de la voz robótica" era un espejismo:** no era que Piper fuese robótico —
  era que **hablaba inglés porque no había voz española configurada** (§0, §7.4). Con una
  voz `es_ES-medium` de verdad, el timbre es aceptable.
- **Decisión global de tier: `medium`** en todos los idiomas (ligero, rápido, +0 peso de
  *high*). La ruta es **cambio-de-voz** (intercambiar `.onnx` por idioma), la barata.
- **Voces v1 (todas medium, por elegir la concreta en el diseño del selector):** en =
  `en_US-lessac-medium` (aceptada); es = `es_ES-davefx-medium` (España); fr/de/it/pt(_PT)/nl
  = mejor *medium* de cada uno.

## 6. Nota adyacente — Whisper (STT), no es lo robótico

`base` es la gama baja para entender bien el español hablado; subir a `small`/`medium`
mejora la transcripción. Pero eso es el eje "¿me entiende cuando hablo?", **no** "¿suena
robótica su voz?". Queda anotado como frente distinto, no como causa del timbre de salida.

---

# 7. Reencuadre — Multilingüe europeo (las TRES capas)

> El frente NO es "arreglar el español". Es **soporte multilingüe europeo** como
> característica de producto — Vokter, agente soberano europeo, hablándole a cada
> europeo en su lengua ("por cercanía, por derecho"). Eso toca **tres capas**, no
> solo la voz: **entender (STT) → pensar (modelo de chat) → hablar (TTS)** — más una
> cuarta, la **selección** que las une. Un idioma solo "existe" en Vokter si funciona
> en las tres. La capa que manda es la del medio: si el modelo no piensa en la lengua,
> da igual que la voz sea perfecta.

## 7.0 Resumen para decidir (el mapa real)

| Idioma | 🗣️ TTS (Piper) | 👂 STT (Whisper base) | 🧠 Chat 3B (probado en vivo) | Veredicto |
|---|---|---|---|---|
| Inglés | 🟢 high | 🟢 excelente | 🟢 nativo | 🟢 **HOY** |
| Español | 🟢 medium (ES) / high (LatAm) | 🟢 bueno | 🟢 nativo | 🟢 **HOY** |
| Francés | 🟢 medium | 🟢 bueno | 🟢 fluido | 🟢 **HOY** |
| Alemán | 🟢 high | 🟢 bueno | 🟢 fluido (sofisticado) | 🟢 **HOY** |
| Italiano | 🟢 medium | 🟢 bueno | 🟢 fluido | 🟢 **HOY** |
| Portugués | 🟢 medium | 🟢 bueno | 🟢 nativo | 🟢 **HOY** |
| Neerlandés | 🟢 medium | 🟢 bueno | 🟢 aguanta (N=3: coherente, afable) | 🟢 **HOY** |
| **Catalán** | 🟢 medium (UPC) — **timbre validado por Bilal** | 🟢 bueno (Whisper va fino en catalán) | 🔴 **aparcado** (N=3: castellanismos + inventos; Bilal: "errores inaceptables") | 🟡 **al próximo swap** (voz ya lista) |
| Polaco | 🟢 medium | 🟢 bueno | 🟡 **grietas** (N=3: bleed de inglés `Dobry question!`, palabras inventadas) | 🟡 **pendiente próximo swap** |
| Griego | 🟠 solo *low* | 🟡 medio | 🔴 **ROTO** (mezcla vi/id a media frase) | 🔴 **NO** (lo bloquea el chat) |
| Gallego | 🔴 **ausente** | 🟠 flojo | 🟠 macarrónico (portugués con toques) | 🔴 **NO** |
| Vasco | 🔴 **ausente** | 🟠 flojo | 🔴 **ROTO** (frases sin sentido, repite) | 🔴 **NO** |
| Nórdicos (sv/no/da), fi, hu, cs, ro… | 🟢 medium (existen) | 🟡 variable | ⚪ **sin probar** (probable techo del 3B) | 🟡 **por probar** |

Leyenda: 🟢 soportable hoy · 🟡 con esfuerzo (falta una capa, pero recuperable) ·
🔴 no realista con el stack actual · ⚪ sin datos.

**Titular:** el cuello de botella NO es la voz — es **el modelo de chat**. TTS y STT se
compran con peso/dinero; el 3B, si no piensa en la lengua, no hay downstream que lo salve.
Y la tensión estratégica: **las lenguas minoritarias que más encarnan "por derecho"
(vasco, gallego) son justo donde el modelo abierto falla, y donde Piper ni tiene voz.**

## 7.1 Capa TTS (hablar) — Piper, mapa europeo

Del catálogo real `voices.json` v1.0.0 (calidad tope por lengua):

- **Buenas (medium/high):** en (high), de (high), es (medium ES / high LatAm), fr, it,
  pt, nl (medium), **ca (medium, UPC)**, pl, sv, no, da, fi, hu, cs, ro, sk, uk, ru, is,
  sl, sr, cy (galés), lb (luxemburgués), tr. La mayoría de Europa está cubierta a *medium*.
- **Floja (solo baja):** **el (griego)** → solo `rapunzelina-low`. Único gran idioma
  europeo sin voz decente.
- **AUSENTES del todo:** **gl (gallego), eu (vasco)**, ga (irlandés), hr (croata). Aquí la
  sospecha de Bilal se confirma: gallego y vasco **no tienen voz Piper**. (Catalán NO era
  el problema: sí la tiene.)
- **Acento:** para español el tope de acento **peninsular** es *medium* (davefx/sharvard);
  la calidad *high* solo existe en acento AR/MX. Mismo patrón puede repetirse en otras.

## 7.2 Capa STT (entender) — faster-whisper

- Whisper es multilingüe (~99 lenguas, incluidas ca/gl/eu/el). El modelo actual es
  **`base` int8** (139 MB sembrado) — **la gama baja**: aguanta los idiomas grandes en
  audio limpio, pero sufre con ruido/acento, y **flojea en lenguas minoritarias**
  (vasco/gallego tienen poco dato de entrenamiento).
- Coste de subir de nivel (int8 aprox.): `base` ~140 MB → `small` ~250 MB (salto grande de
  precisión) → `medium` ~770 MB (fuerte, casi duplica el peso del `.deb`) → `large-v3`
  ~1,5 GB (lo mejor, sobre todo para minoritarias, pero pesado). **Nota:** el `.transcribe()`
  actual **no pasa `language=`** → autodetecta; para lenguas parecidas (gl↔pt, ca↔es) fijar
  el idioma ayudaría a no confundirlas.

## 7.3 Capa CHAT (pensar) — `llama3.2:3b`, PROBADO EN VIVO

> **⚠️ Actualización de método (2026-08-03):** la primera ronda fue **1 muestra por
> lengua** con una consigna fácil (explicar privacidad). Regla propia: el 3B no es
> determinista → medir N≥3 y con tareas variadas. Segunda ronda **N=3 en los verdes
> menos obvios (ca/nl/pl)** con tareas de asistente real (explicar + aconsejar +
> escribir nota corta) **cambió el cuadro**: la muestra fácil los hacía verdes; bajo
> carga cotidiana, **catalán y polaco se agrietan** (castellanismos/inventos en ca;
> bleed de inglés `Dobry question!` en pl), **holandés aguanta**. Muestras en
> `/home/harry/vokter-voz-test/3b-verds-ca-nl-pl.txt`. Lección confirmada: la tarea
> fácil miente; hay que probar con tareas de asistente reales.

Prueba real contra Ollama (11434), misma consigna nativa en cada lengua ("explica en 2
frases por qué importa la privacidad digital, responde solo en X"):

- **Oficiales del 3B (Meta lista 8: en/de/fr/it/pt/es + hi/th):** salieron **nativos**.
- **No oficiales que aun así SALEN BIEN:** **neerlandés** (fluido), **catalán** (limpio),
  **polaco** (fluido, fallo menor de caso). El 3B generaliza a vecinos romances/germánicos.
- **Se ROMPEN:**
  - **Griego:** cambia de código a media frase, mete palabras vietnamitas/indonesias
    (`vì`, `seperti`, `τρίτες πάρtees`, `ατομική tựdoξία`) → inusable.
  - **Vasco:** gramática rota, repite la misma frase sin sentido → inusable.
  - **Gallego:** macarrónico — en realidad **portugués** con algún gallego (`informação`,
    `nas nossas interações`) → no es gallego limpio.

**Conclusión de la capa:** el 3B da **~9 lenguas europeas de calidad** (en/es/fr/de/it/pt/
nl/ca/pl) y se cae en griego/vasco/gallego. Nórdicos/finés/húngaro/checo/rumano: **sin
probar**, pero el patrón (no oficiales + baja densidad de datos) hace prever degradación —
hay que medirlos, no asumirlos.

## 7.4 Capa SELECCIÓN — cómo se conectan (o no) hoy

Hoy las tres capas están **desacopladas y NO unificadas**:

- **Chat:** hay UN ajuste `cfg["language"]` (`#cfg-language` en Ajustes: auto/en/es/fr/de/
  it/pt/nl/pl). Solo alimenta el system prompt (`agent_config.py:69` → "Always respond in
  {lang}", o si es `auto`, "responde en el idioma de la pregunta"). Es lo ÚNICO cableado.
- **TTS (voz):** **ignora** ese ajuste. `PIPER_VOICE` es un env fijo (`en_US-lessac-medium`),
  el orquestador no lo cambia → **la voz siempre habla inglés**, elija lo que elija el usuario.
- **STT:** **ignora** ese ajuste. `whisper.transcribe()` no recibe `language=` → autodetecta.

O sea: el "idioma del agente" **no existe como concepto único** hoy. El usuario puede pedir
respuestas en francés, pero le seguirán llegando en **voz inglesa**, y su micro se
autodetecta a ciegas. La decisión de arquitectura pendiente: **un solo selector de idioma
que gobierne las tres capas a la vez** (responder en X + voz Piper de X + pista de idioma X
al STT), con la lista de idiomas **recortada a los que pasan las tres capas** (los 🟢),
no a los 8 que hoy ofrece el desplegable (varios de los cuales el 3B sí habla, pero cuya
voz no existe todavía).

## 7.5 Decisión de alcance y qué queda por decidir

- **✅ DECIDIDO (Bilal, 2026-08-03):** griego, vasco y gallego **fuera de momento**
  (los rompe el chat; gallego/vasco ni tienen voz Piper). Se reevalúan con el próximo swap.
- **🔬 REVISADO con N=3 + VEREDICTO DE BILAL (2026-08-03):**
  - **Neerlandés → confirmado 🟢** (aguanta tareas cotidianas).
  - **Polaco → aparcado** al próximo swap (bleed de inglés `Dobry question!` + inventos).
  - **Catalán → aparcado** al próximo swap. Bilal juzgó el catalán del 3B **en audio**
    (voz catalana UPC) y dictaminó: **"errores inaceptables"** en el modelo. PERO el
    **timbre de la voz catalana SÍ le vale** → cuando un swap recupere el catalán del
    chat, **la voz ya está validada, no hay que re-testearla**.
- **✅ ALCANCE v1 CERRADO (2026-08-03): 7 idiomas — `en · es · fr · de · it · pt · nl`.**
  Todos con las tres capas OK y **medidos por prueba, no estimados**:
  - **nl:** N=3 con tareas difíciles (explicar+aconsejar+nota) → aguantó.
  - **es·fr·de·it·pt:** N=1 fácil **+ N=2 con la prueba dura** (aconsejar + nota corta, la que
    reventó a ca/pl) → coherentes, nativos, sin inventos ni bleed. Muestras en
    `/home/harry/vokter-voz-test/3b-verds-oficiales-hardtask.txt`.
  - **en:** lengua primaria del modelo, riesgo cero.
  - ⚠️ **Matiz portugués:** el 3B *piensa* bien portugués pero **mezcla registro PT-PT/PT-BR**
    (`você` brasileño con formas europeas). No descalifica (no es fallo de pensamiento como
    ca/pl); se ataja con voz `pt_PT` + empujón "portugués europeo" en el system prompt. A
    vigilar en el diseño del selector.
- **🔗 Catalán y polaco → PENDIENTES DEL PRÓXIMO SWAP (candidatos #1 a recuperar).** Descartados
  de v1 por errores inaceptables del 3B (**probados, no estimados**), NO por falta de voz/STT
  (ambos listos: catalán con voz UPC ya validada por Bilal; polaco con voz medium). El único
  eslabón que falla es el CHAT. Esto **liga el descarte con el radar de modelos**: el swap deja
  de justificarse solo por afabilidad — tiene **razón con nombre**, recuperar **catalán y
  polaco** con un modelo de mejor cobertura europea (p.ej. Salamandra-BSC, entrenado con
  ca/eu/gl). Disparador concreto = "cuando exista un modelo pequeño que piense bien ca/pl sin
  romper los 7 actuales".
- **Dato colateral:** que Bilal acepte el timbre de `ca_ES-upc_ona-medium` (Piper *medium*)
  es la primera señal de que **Piper *medium* puede ser aceptable a su oído** — matiza el
  "Piper es robótico de suyo". Pendiente su veredicto de los 4 WAV en/es de §5 para
  generalizarlo.

**📡 Radar de modelos (APARCADO 2026-08-03, para retomar):** qué modelo local sustituiría
a `llama3.2:3b` (candidatos Qwen3-4B / Gemma3-4B / europeos soberanos tipo Salamandra-BSC /
EuroLLM) y con qué disparador. **Dos razones que lo justificarán cuando toque: (1) el
multilingüe** — recuperar los verdes que el 3B agrieta (catalán/polaco) y quizá los rojos
(griego/gallego/vasco: Salamandra del BSC se entrenó con ca/eu/gl); **(2) la afabilidad** —
conversación menos robótica, menos bleed de idioma. Confirmado ya desde código: chat y
embeddings son **independientes** (swap NO re-indexa) y el swap es **barato** (cambiar un
string; Ollama aplica la plantilla del modelo; los modelos no van sembrados en el `.deb`).

Pendiente de decidir (sin tocar código aún):

1. **STT:** ¿subir de `base` a `small` (mejor comprensión, ~+110 MB) para que "entender"
   no sea el eslabón flojo en los 9 verdes?
2. **Unificación del selector:** un solo idioma → tres capas (responder en X + voz Piper
   de X + pista de idioma X al STT). Diseño pendiente.
3. **Voces:** elegir la voz Piper concreta por idioma (y, en español, el acento) — depende
   de la prueba de oídos de §5.

**Nada de esto se implementa hasta el OK de Bilal.** Este documento solo fija el mapa.

---

# 8. DISEÑO — Unificación del selector (un idioma → tres capas)

> Estado: DISEÑO, sin código. Objetivo: hoy las tres capas están **desconectadas** — el
> selector solo cambia la respuesta escrita; la voz está clavada en inglés; el STT
> autodetecta a ciegas. Meta: **UN** ajuste de idioma que gobierne las tres. Alcance = los
> 7 de v1 (`en·es·fr·de·it·pt·nl`) + `auto`.

## 8.0 Fuente única de verdad (ya existe)

El ajuste `agent_config.language` **ya existe** (tabla `agent_config`, DB **cifrado**,
key-value): **por-usuario, persiste reinicios** (§verificado en `agent_config.py`). Hoy solo
lo lee `build_system_prompt`. El diseño NO añade almacenamiento nuevo — hace que **las otras
dos capas lean la MISMA clave**. Responde ya a §3 (persistencia) y §4 (por-usuario): sí y sí.

## 8.1 Tabla de mapeo (el corazón del diseño)

Un único diccionario `idioma → (voz Piper medium, código Whisper, nota chat)`:

| Sel. | Voz Piper (medium) | Whisper `language=` | Prompt chat |
|---|---|---|---|
| en | `en_US-lessac-medium` | `en` | "respond in English" |
| es | `es_ES-davefx-medium` (España) | `es` | "respond in Spanish" |
| fr | `fr_FR-siwis-medium` | `fr` | "respond in French" |
| de | `de_DE-thorsten-medium` | `de` | "respond in German" |
| it | `it_IT-paola-medium` | `it` | "respond in Italian" |
| **pt** | **`pt_PT-tugão-medium` (Europeo)** | `pt` | **"respond in European Portuguese (Portugal)"** |
| nl | `nl_NL-ronnie-medium` | `nl` | "respond in Dutch" |
| auto | (detectar, ver 8.2) | (omitir → autodetecta) | "mirror the question's language" |

(Los speakers concretos de fr/de/it/nl son provisionales — todos *medium*, elección final
tras una escucha rápida; es/en ya fijados por Bilal.)

**✅ Confirmación por oído (Bilal, 2026-08-03):** escuchadas fr `siwis`, de `thorsten`, it
`paola`, nl `ronnie` → **las 4 aceptadas** (+ es `davefx`, en `lessac` ya juzgadas). Tabla
confirmada por oído para 6 de 7.
**⚠️ Excepción PT:** Bilal oye `pt_PT-tugão-medium` como **brasileño**. Pero **tugão es la
ÚNICA voz pt_PT de Piper** (las otras 4 pt son pt_BR); sus metadatos dicen `pt_PT/Portugal`.
Cambiar el key no ayuda (no hay otra europea). Ni Bilal ni el agente son oído nativo pt_PT →
señal blanda. **Decisión v1: mantener tugão (única europea; el prompt ya fuerza texto pt_PT),
marcada PROVISIONAL — confirmar con oído nativo antes del seed final, y candidata #1 a mejora
de voz (posiblemente fuera de Piper) como deuda de producto** — el análogo, en la capa VOZ, de
lo que ca/pl son en la capa CHAT.

## 8.2 Cómo cambia cada capa

**Chat (escrito) — se REUTILIZA tal cual.** `build_system_prompt` ya lee `language`. Único
retoque: la línea de `pt` pasa a "European Portuguese" (§5 matiz PT). Lista del selector baja
de 8 a 7 (quitar `pl`, aparcado). Coste: mínimo, ya funciona.

**Voz (TTS) — el cambio real.** Hoy `PIPER_VOICE` es un env fijo y `_voice` se cachea UNA
vez. Rediseño:
- `/api/voice/speak` **lee `agent_config.language`** (fuente única; el cliente no manda el
  idioma → no hay que fiarse del navegador) y resuelve la voz por la tabla 8.1.
- **Caché por `voice_id`** (hoy es una sola instancia global). LRU de tamaño 1–2: cargar en
  vago la voz del idioma activo (~1,5 s, ~60 MB RAM), no mantener las 7 cargadas (7×60 MB
  reventaría los 8 GB). El usuario típico usa 1 idioma → 1 voz cargada.
- Si el `.onnx` del idioma no está en disco → **`_download_voice()` (ruta que YA existe)** lo
  baja de HuggingFace la 1ª vez. Ver decisión de peso 8.3.
- **Caso `auto` (la ÚNICA lógica nueva de riesgo de todo el diseño):**
  - **✅ Recomendado v1 — sin detección:** `auto` gobierna solo chat (mirror) y STT
    (autodetecta); la VOZ usa el **último idioma concreto elegido** (o inglés si nunca se
    eligió). Así v1 no depende de ninguna librería de detección de idioma nueva → **cero
    riesgo, cero dependencia**. Coste: si escribes en francés con la voz en "último=español",
    la voz sonará española sobre texto francés (raro, pero acotado — y el usuario que mezcla
    idiomas normalmente fija uno).
  - **v2 — con detección:** el backend detecta el idioma del texto de respuesta (heurística/
    lib pequeña) y elige la voz v1 más cercana, *fallback* inglés. Mejor ilusión, pero mete
    una dependencia y un modo de fallo nuevos. **Aplazar a después de v1.**
  - Regla de diseño: **v1 no introduce detección de idioma.** Todo lo demás del selector es
    cableado sobre piezas que ya existen.

**STT (Whisper) — un hilo, no un modelo nuevo.** Whisper `base` es **UN modelo multilingüe
que ya cubre los 7** (no se multiplica por idioma, a diferencia de la voz). Rediseño: si el
idioma es concreto, pasar `language=<código>` a `transcribe()` (más preciso); si es `auto`,
omitirlo (autodetección de hoy). Cambio conceptual de una línea.

## 8.3 ⚠️ DECISIÓN DE PESO (la que Bilal decide)

**Solo la VOZ multiplica peso.** Whisper es 1 modelo para los 7 (ya sembrado, 139 MB). El
`.deb` hoy = 388 MB e **incluye ya `en_US-lessac-medium` (61 MB) + whisper base**. Cada voz
Piper medium ≈ 60 MB.

| Opción | Qué se siembra en el `.deb` | Peso `.deb` | Offline tras setup | Peso muerto |
|---|---|---|---|---|
| **A** Sembrar las 7 | 7 voces | **~750–800 MB** ⚠️ | Sí, los 7 | 6 voces que el usuario no usa (~360 MB) |
| **B** Inglés + resto on-demand | 1 voz (inglés, ya está) | **~388 MB** (sin cambio) | Sí, en su idioma* | 61 MB (inglés, muerto para no-anglófonos) |
| **C′** Nada + bajar **el idioma elegido en el onboarding** | 0 voces | **~327 MB** (el más ligero) | **Sí, en su idioma** | **0** |
| ~~C~~ Nada + bajar en 1er *uso* | 0 | ~327 MB | **No** (rompe offline) | 0 |

\* En B, el idioma del usuario se baja on-demand al elegirlo (durante el onboarding, con red).

**La frontera real es B vs. C′** (A es demasiado `.deb`; C es C′ mal hecho — bajar en 1er uso
en vez de en el onboarding, y por eso rompe "habla offline out-of-the-box"). Clave que las dos
comparten: el 1er arranque **ya exige internet** (baja ~2 GB de modelos Ollama por
`ensure_models`) → bajar ~60 MB de la voz elegida AHÍ, con la barra de progreso que YA existe
(Fase 3.3-D), es ruido. Tras el setup, la voz queda **local para siempre** = offline real. Y
Chat+STT nunca esperan red (Whisper es 1 modelo multilingüe ya sembrado).

**El desempate es de Bilal** — una sola pregunta:

> **¿Vale una voz inglesa SIEMPRE presente los 61 MB de `.deb` que serán peso muerto para casi
> todos tus usuarios (europeos no anglófonos, que es la tesis)?**

- **Sí, quiero el colchón** → **Opción B.** El inglés está pase lo que pase: sirve de
  *fallback* si la detección de `auto` falla y de red de seguridad si la descarga del
  onboarding se cae. Cuesta 61 MB muertos para un francés/holandés.
- **No, coherencia local-first pura** → **Opción C′.** `.deb` mínimo (327 MB), cero peso
  muerto, cada quien con SU voz. A cambio, si la descarga del onboarding falla y el idioma no
  es inglés, no hay voz de reserva hasta reintentar (chat+STT siguen; solo la voz espera).

**Mi recomendación: C′**, por coherencia con la tesis (cada europeo su lengua, nada de inglés
por defecto que casi nadie usa) y `.deb` más ligero — *con la condición* de un reintento
robusto de la descarga de voz (si falla en onboarding, que se reintente y mientras la UI diga
"voz no disponible aún", sin romper chat/STT). Si esa robustez preocupa, **B** es el seguro
barato. Decides tú.

### ✅ DECIDIDO (Bilal, 2026-08-03): C′ — con la robustez como REQUISITO, no opcional.

Motivo de Bilal: la voz inglesa siempre-presente de B son **61 MB de peso muerto para
exactamente su público** (europeos no anglófonos); un `.deb` que carga una voz que un
francés/alemán nunca usa contradice "cada ciudadano en su lengua". C′ es coherente y más
ligero (327 MB). **Sin la robustez, cambiaría a B** → la robustez es condición de contrato.

**Comportamiento del fallo de descarga (spec de C′, confirmado):**
- **Independencia estructural (garantía del código, no buena voluntad):** chat (`/api/chat`),
  STT (`/api/voice/transcribe`) y TTS (`/api/voice/speak`) son endpoints y modelos SEPARADOS.
  Un `.onnx` de voz que no bajó **no puede** afectar a chat ni STT. Además, bajo C′ **Whisper
  `base` SÍ se siembra** (1 modelo multilingüe, 139 MB, no multiplica) → el STT nunca depende
  de red. Solo las voces Piper son on-demand.
- **El usuario usa Vokter ENTERO por texto sin la voz:** chatea, RAG, documentos y **micro
  (STT)** funcionan; lo único ausente es que Vokter responda en voz alta. Degradación parcial,
  jamás Vokter roto.
- **La descarga es en el ONBOARDING** (proactiva, barra 3.3-D), no al primer ▶. Si falla, el
  onboarding **se completa igual**; la voz queda "no disponible".
- **`speak()` NO hace descarga bloqueante:** si falta el `.onnx`, responde limpio
  `voice_not_ready` (no un 500). El ▶ pinta **"Voz no disponible aún — [Reintentar]"**, nunca
  cuelga ni da error feo.
- **Reintento = los dos:** (a) **automático oportunista** en cada arranque (el arranque ya
  comprueba presencia y baja si falta) → cura fallos transitorios sin intervención; (b)
  **manual** (botón "Reintentar") en el ▶ y en Ajustes → control cuando el usuario recupera red.

## 8.4 Defecto de primer arranque (§4)

**Recomendación (✅ aprobada por Bilal):** en el primer arranque, **leer el locale del sistema
(`$LANG`)** y **pre-seleccionar** el idioma v1 correspondiente en el paso "Choose your
language" del wizard (que ya existe). **El usuario confirma**, no se aplica en silencio.

**`$LANG` fuera de los 7 (confirmado):** cae a **inglés como defecto pre-seleccionado**. La
lógica solo elige DENTRO de los 7 → es estructuralmente imposible pre-seleccionar un idioma
ausente de la lista. Mapeo: `pt_BR`→`pt`, `en_GB`→`en`, etc.; y **fuera** (griego, ruso, vasco,
gallego, **y también los aparcados ca/pl**) → **inglés**. Consistencia: los aparcados caen a
inglés en el pre-select sin caso especial, porque no están en v1.

**Distinción importante (no confundir dos ejes):**
- **Idioma del AGENTE** (chat+voz+STT) = lo que este diseño unifica → los 7 disponibles ya.
- **Idioma del CHROME de la UI** (textos del wizard/botones, `I18N`/`ONBOARDING_LANGS`) = otra
  pista, hoy solo `en`+`es` traducidos. Un usuario puede tener el AGENTE en francés con el
  CHROME en inglés. v1 unifica el eje del agente; el i18n del chrome sigue su ritmo (memoria
  de estrategia i18n). No bloquear uno con el otro.

## 8.5 Matiz PT dentro del diseño (§5)

Dos puntos, ya integrados arriba: (a) TTS mapea `pt → pt_PT-tugão-medium` (voz **europea**,
no BR); (b) chat, cuando `language==pt`, emite "respond in European Portuguese (Portugal)" en
vez del genérico. Caveat honesto: la garantía PT-PT **solo aplica con `pt` elegido explícito**;
bajo `auto`, si detecta portugués, no puede forzar registro (mirror puro). Aceptable para v1.

## 8.6 Resumen del esfuerzo (para dimensionar, no implementar)

- **Chat:** ~1 línea (variante PT) + recortar lista a 7. Trivial.
- **STT:** ~1 línea (`language=` cuando concreto). Trivial.
- **TTS:** el grueso — caché por `voice_id` + leer `agent_config` + (si `auto`) detección de
  idioma. Contenido a `app/voice/piper.py` + tabla de mapeo. La UI (selector) ya existe;
  se le añaden 2 comportamientos (voz+STT) al `onchange` que hoy solo toca el chat.
- **Empaquetado:** según decisión 8.3. Opción B = **cero cambios de empaquetado**.

**Sin implementar hasta OK de Bilal.** Decisión abierta clave: **8.3 (peso) → recomiendo B**.

## 8.7 Estado de implementación (por etapas)

- **✅ ETAPA 1 — Backend TTS+STT (2026-08-03).** `app/languages.py` (tabla única de los 7);
  `speak()` lee `agent_config.language`, resuelve voz, **caché LRU tope 2** (probado: nunca
  >2, LRU expulsa la vieja, RAM acotada), **`voice_not_ready` no-bloqueante** (probado por
  curl: voz ausente = 503 en ~2 ms, sin descarga, chat intacto); Whisper recibe `language=`
  cuando concreto (`None`→autodetecta); prompt PT = "European Portuguese (Portugal)". Voces
  fr/de/it/pt/nl **confirmadas por oído** por Bilal (pt = `tugão`, único europeo, provisional).
- **✅ ETAPA 2 — UI del selector (2026-08-03).** `pl` fuera del selector de Ajustes (quedan
  `auto`+7); ese selector ya gobierna las 3 capas al guardar (backend etapa 1). ▶ maneja
  `voice_not_ready` → estado **⚠ "Voice not available yet — click to retry"** (degradación
  limpia + reintento manual). Onboarding: **decoplados los DOS ejes** — `AGENT_LANGS` (7,
  gobierna chat+voz+STT) vs `ONBOARDING_LANGS` (chrome, en/es); el paso de idioma ofrece los
  7, **pre-select por locale (`navigator.language` = proxy de `$LANG` en el renderer Electron)
  mapeado deny-closed a los 7** (fuera → inglés; probado: griego/ruso/sueco/ca/pl/auto → en),
  el chrome sigue solo si está traducido. Comentario prominente en el código advierte de NO
  volver a fundir los dos ejes (era el bug del wizard). Sintaxis JS verificada.
- **✅ ETAPA 3 (motor) — `app/voice/fetch.py` (2026-08-03).** Primitiva `ensure_voice` +
  endpoints `POST /api/voice/ensure` (no bloqueante) y `GET /api/voice/state`. Descarga desde
  HF `v1.0.0`, **verifica size+md5 del `voices.json` fijado** (checksums NO en la tabla →
  swap = una línea, probado: `grep md5 languages.py` = 0), **temp único → verificar → `os.replace`**,
  **guard in-flight** por voice_id. Árbol de fallos **probado en dev** (servidor local
  manipulable): baseline→ready; sin red / 404 / trunc / **corrupto(md5)** → `error`, AUSENTE,
  **cero temps sueltos**, chat+STT intactos; **concurrencia**: 2 `ensure` a la vez → **1 sola
  descarga**, sin temp compartido. Happy-path REAL contra HF (de_DE-thorsten, 63 MB, md5 en
  disco == voices.json, idempotente). **Pendiente (cableado de disparadores, no el motor):**
  onboarding-finish → `ensure`; botón ⚠ → `ensure` (hoy sólo reintenta `speak`); orquestador
  arranque oportunista (§9.1, reusa barra 3.3-D desde 2º lanzamiento).
- **⏳ ETAPA 4 — Empaquetado C′** (quitar `voice-seed` del `.spec`/`package.json`, `.deb`
  ~327 MB, prueba E2E en VM). Pendiente.

---

# 9. DISEÑO — Etapa 3: descarga de voz con reintento (la robustez de C′)

> Estado: DISEÑO, sin código. Es la etapa de MÁS RIESGO del frente: bajar un `.onnx` de
> internet sin dejar nunca un fichero a medias que `_present()` confunda con "listo". El
> riesgo real no es "no hay red" (eso degrada limpio) — es **el `.onnx` corrupto/truncado**.
> Todo el diseño gira en torno a **verificar-antes-de-promover**.

## 9.0 Una primitiva, tres disparadores

Toda la descarga vive en UNA función idempotente — llámala `ensure_voice(lang)` (backend):
- voz ya presente y verificada → *ready* al instante.
- ausente → descarga → verifica → promueve atómicamente → *ready*; si algo falla → limpia y
  *error* (nunca deja fichero a medias).

La disparan **tres** sitios, todos la misma primitiva (sin duplicar lógica):
1. **Fin del onboarding** (in-app): el usuario confirmó idioma → se baja su voz con progreso.
2. **Cambiar idioma en Ajustes** (in-app, §9.5): misma primitiva, disparada al guardar.
3. **Arranque oportunista** (loading screen) + **botón ⚠ Reintentar** (in-app): si la voz del
   idioma actual falta, se (re)intenta. Cura fallos transitorios sin intervención + a demanda.

## 9.1 De dónde y con qué integridad (§Q1)

- **Origen:** HuggingFace `rhasspy/piper-voices`, **tag inmutable `v1.0.0`** (no `main` — así
  el binario de un usuario baja SIEMPRE el mismo fichero, reproducible). Ruta derivada del
  `voice_id`: `{base}/{lang}/{lang_full}/{speaker}/{quality}/{voice_id}.onnx` (+ `.onnx.json`).
- **Integridad — SÍ se verifica (no se confía):** `voices.json` de Piper trae **`size_bytes` +
  `md5_digest`** por fichero. Tras bajar: comprobar tamaño Y md5; **si no cuadra → descartar y
  contar como AUSENTE** (un `.onnx` a medias es peor que ausente → nunca se trata como listo).
  - **⚠️ SUB-DECISIÓN DE BILAL — de dónde salen los checksums:** dos opciones, con una tensión
    real contra "cambiar una voz = una línea" (que prometimos barato):
    - **(a) Hornear** los 14 pares size/md5 en `languages.py`. Verificación sin red. **Pero**
      cambiar una voz pasa a ser 2 cosas (key + md5 nuevo), y **si alguien cambia el key y
      olvida el md5 → TODA descarga de esa voz falla verificación → `voice_not_ready`
      permanente que solo se ve como ⚠**. Footgun silencioso en la operación que dijiste barata.
    - **(b) ✅ Recomendada — leer los checksums del `voices.json` fijado (`v1.0.0`) en el
      momento de la descarga**, cacheándolo en disco. La tabla sigue siendo **solo voice-id →
      swap = una línea de verdad**. Y no añade dependencia offline: la verificación solo ocurre
      DURANTE una descarga, que ya requiere red → bajar el `voices.json` (~200 KB) ahí es gratis.
    - Mi voto: **(b)** — preserva el swap de una línea que valoraste, con verificación igual de
      robusta. Tú decides.
- **Corrección + verificación de la premisa de Q1 (barra 3.3-D):** verifiqué la secuencia de
  `orchestrator.main()`: **`db_key` está disponible en la línea 600, ANTES de `ensure_models`
  (602)**. O sea el orquestador SÍ puede abrir el DB cifrado, leer `agent_config.language` y
  bajar la voz en la MISMA fase que los modelos → **el disparador de arranque SÍ reutiliza la
  barra 3.3-D** (mejor de lo que temíamos). Dos matices honestos: (i) solo desde el **2º
  lanzamiento** (en el 1º el idioma aún no está elegido — eso lo cubre el disparador in-app
  post-onboarding); (ii) el onboarding/Ajustes **in-app** siguen usando su **propio progreso**
  (son otra superficie, la página hablando con el backend), NO la barra de la loading screen.

## 9.2 Atomicidad — el corazón anti-corrupción (§Q3)

1. Bajar a un **temporal ÚNICO** (`mkstemp` en el dir de piper) — no un `.part` de nombre fijo,
   para que dos intentos no puedan pisar el mismo fichero (ver §9.2b concurrencia).
2. **Verificar** size + md5 de AMBOS temporales (.onnx y .onnx.json).
3. Solo si ambos verifican → `os.replace(temp → final)` (rename atómico en el mismo FS).
4. Cualquier fallo (red, verificación, disco) → **borrar los temporales**; nunca se promueve.

`_present()` (etapa 1) comprueba las rutas **finales** (`.onnx` **y** `.onnx.json`). Como solo
se promueve tras verificar, **`_present()` no puede ver jamás un fichero a medias como listo**.
Es el patrón `os.replace` del viejo `_download_voice`, PERO con verificación intercalada y temp
único.

**Medio-promovido explícito (pre-empto la pregunta obvia):** si el proceso muere ENTRE los dos
renames (`.onnx` promovido, `.onnx.json` no), `_present()` exige **los DOS** finales → lo lee
como **AUSENTE**, no como listo → el reintento re-baja y re-promueve. Un `.onnx` huérfano sin su
`.json` nunca se sirve. La no-atomicidad de "dos ficheros" queda cubierta por el AND de `_present`.

## 9.2b Concurrencia — que dos descargas no se pisen (§añadido al árbol de fallos)

Tres disparadores pueden pedir la MISMA voz casi a la vez (onboarding-finish + usuario
impaciente pulsando ⚠ + arranque oportunista). Cobertura en dos niveles:
- **Entre PROCESOS (orquestador vs backend): por secuencia, no por lock.** El fetch de arranque
  (orquestador) ocurre ANTES de `start_backend` (línea 604) → el backend aún no sirve → nunca
  coincide en el tiempo con un fetch in-app (que es post-UI). No hay dos procesos bajando a la vez.
- **Dentro del backend (onboarding + ⚠ + Ajustes): guard in-flight.** Un dict/lock por
  `voice_id`: el primer `ensure_voice` marca "en curso" y baja; los siguientes ven `downloading`
  y **esperan/consultan estado**, no arrancan una segunda descarga. Coalescen en una sola.
- **Cinturón y tirantes:** como cada intento baja a un **temp único** (§9.2-1), aunque algo
  patológico solapara, no hay `.part` compartido que corromper — a lo sumo dos descargas
  redundantes, cada una a su temp, y gana el `os.replace` del que verifique; el otro se descarta.

## 9.3 Árbol de casos de fallo (§Q2) — qué pasa EXACTAMENTE

| Fallo | Qué ocurre | Estado tras el fallo |
|---|---|---|
| **Sin red** al empezar | `ensure_voice` falla al conectar, rápido | `.part` no creado; voz AUSENTE; chat+STT OK; ▶ = ⚠ retry |
| **Red cae a media descarga** | `.part` incompleto | `.part` **borrado**; voz AUSENTE (no corrupta); retry re-baja de cero |
| **Servidor caído / 404 / 5xx** | nada se promueve | `.part` (si hubo) borrado; AUSENTE; retry luego |
| **Baja completo pero CORRUPTO** (truncado, bit-rot) | size/md5 **no cuadran** | `.part` **borrado**, NO se promueve; AUSENTE; retry | 
| **Disco lleno** a media escritura | escritura falla | temp borrado; AUSENTE |
| **Llamadas concurrentes** (onboarding + ⚠ + arranque) | guard in-flight coalescen; entre procesos, por secuencia | una sola descarga; nunca temp compartido (§9.2b) |

Invariantes en TODOS los casos:
- **El onboarding NUNCA bloquea:** se completa; la voz queda pendiente. Chat + STT (Whisper
  sembrado) funcionan enteros. Solo la salida por voz muestra ⚠.
- **Nunca queda un `.onnx` corrupto en su sitio** (verificar-antes-de-promover + limpiar `.part`).
- **Reintento:** el botón ⚠ dispara `ensure_voice` (descarga limpia de cero); el arranque
  oportunista la dispara en cada lanzamiento si el idioma actual sigue sin voz.

## 9.4 Orden respecto al 2 GB de modelos (§Q4)

Son **momentos distintos, por fuerza**, no simultáneos:
1. **Arranque / loading screen:** `ensure_models` baja ~2 GB (chat + embed). Necesario para que
   el app funcione. El idioma aún NO está elegido (o es el pre-guess del locale).
2. **App abierta → onboarding:** el usuario confirma idioma.
3. **Tras confirmar (in-app):** `ensure_voice` baja los ~60 MB de esa voz, con progreso propio.

Consecuencia: un fallo de red en (1) **impide el app entero** (sin modelos no hay chat — es el
comportamiento actual, no lo cambia la etapa 3); un fallo en (3) **solo degrada la voz**
(chat+STT siguen). Ambos exigen red, pero son secuenciales → un fallo de voz no arrastra al chat.

*Alternativa considerada y descartada:* pre-bajar en (1) la voz del locale-guess junto a los
modelos (una sola barra). Descartada porque el guess puede fallar (el usuario cambia en el
wizard) → descarga desperdiciada. Mejor bajar tras confirmar (3); el arranque oportunista es la
red de seguridad. (Como voz = ~60 MB, si más tarde se quiere el pre-fetch, es barato de añadir.)

## 9.5 Cambiar idioma DESPUÉS del onboarding (§Q5)

**Mismo mecanismo**, distinto disparador: al guardar un idioma nuevo en Ajustes cuya voz falta,
se llama a `ensure_voice` (progreso in-app), igual que en el onboarding. Y si el usuario no
espera, el `speak()` siguiente da `voice_not_ready` → ▶ = ⚠ retry, y el arranque oportunista la
completa. Una sola primitiva cubre onboarding, Ajustes, retry y arranque — sin caminos paralelos.

## 9.6 Modelo de estado (para la UI)

`GET /api/voice/state` → `{status: ready | downloading | absent | error, progress?}` para el
idioma actual. `POST /api/voice/ensure` (idempotente) arranca la descarga si hace falta. El ▶
(⚠/listo), el onboarding y Ajustes leen el MISMO estado → coherencia entre las tres superficies.

**Sin implementar hasta OK de Bilal.** Riesgo cubierto: `.onnx` corrupto imposible de ver como
listo (verificar-antes-de-promover, §9.2); fallo de red degrada limpio (§9.3); onboarding nunca
bloquea.
