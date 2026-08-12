# Vokter — Arquitectura de Cómputo

Documento vivo. Decisiones y observaciones sobre el modelo de cómputo de Vokter
(local-first, soberano) y el seguimiento del ecosistema. Dos ejes, dos secciones —
no mezclarlos:

- **Cómputo Modo 1 / Modo 2** — cómo evoluciona el terreno de la apuesta (proveedores,
  precondiciones, qué cabe en local). No es "¿copiamos?", es "¿cómo cambia lo posible?".
- **Proyectos externos (radar)** — proyectos-agente concretos: ¿adoptar código/patrones o
  no? Veredicto **cantera** vs **radar**.

---

## Cómputo Modo 1 / Modo 2 — proveedores y precondiciones

- **Modo 1** — inferencia local en la máquina del usuario (p. ej. ThinkPad 8 GB). Depende de
  la tendencia "modelos pequeños cada vez mejores" (Qwen/Gemma 4B).
- **Modo 2** — cómputo confidencial soberano: correr un modelo de peso ABIERTO nivel-frontera
  en un TEE que el usuario **atestigua** criptográficamente (no "un proveedor promete no mirar").

### Wafer — wafer.ai/blog/kimi-k3-mi355x ("Is memory the moat?", jul 2026) — proveedor candidato Modo 2

Post de infra: empresa de inferencia sirviendo Kimi K3 en GPUs AMD MI355X (~952 tok/s/nodo,
más barato por dólar que NVIDIA Blackwell). Ingeniería de servidor, NADA que copiar para el
código de Vokter.

**Valida la precondición del Modo 2 (jul-2026):** los modelos abiertos frontera YA rivalizan
con los cerrados — DeepSeek V4-Pro, GLM5.2 (near-Opus), Kimi K3 (nivel Fable/Sol). El Modo 2
(cómputo confidencial soberano) exige un modelo de peso abierto nivel-frontera: no puedes meter
Claude/GPT en un TEE que verificas, sí uno abierto. Esa precondición ya está cumplida — dejó de
ser apuesta.

**Refuerza el límite del Modo 1 (sin cambio):** esos modelos son GIGANTES. Kimi K3 = 2.8T
params, >1.5 TB VRAM; ni un nodo B200 (8 GPU) lo aguanta, y la tendencia frontera-abierta es a
MÁS grande. El Modo 1 (ThinkPad 8GB) sigue dependiendo de la OTRA tendencia: modelos pequeños
mejores (Qwen/Gemma 4B).

**Wafer como proveedor candidato del Modo 2 — cautela de soberanía en ROJO:** son el tipo de
infra que el Modo 2 necesitaría (modelos abiertos, API OpenAI-compatible, promocionan "zero
data retention"). PERO retención-cero POR CONTRATO ≠ cómputo confidencial VERIFICABLE: confías
en que no guardan tus datos, no lo atestiguas criptográficamente (enclave/TEE). El Modo 2 real
es "corres el modelo abierto en un TEE que TÚ atestiguas", no "un proveedor promete no mirar".
Wafer es PELDAÑO (mejor que OpenAI), NO destino. Candidato a evaluar, con esa distinción clara.

**Observación (refuerza la tesis):** el título "Is memory the moat?" lo plantean desde el
hardware (¿la HBM del chip es la ventaja?). Para Vokter el foso es otro y es la tesis entera:
NO la memoria del chip ni la inteligencia bruta, sino que la memoria del USUARIO viva cifrada
en su máquina, con gates, sin salir. Panda la desactivó; los cloud la tienen en sus servidores;
Vokter la blinda localmente. Ese foso no depende de chip ni modelo.

> Nota: falta la entrada de **Kimi K3 (Unsloth, correr K3 en 1-bit local)** de una sesión
> anterior — buscada en memoria/docs/transcripciones/outputs el 2026-08-03 y NO encontrada en
> ningún sitio. Su hueco natural es aquí, junto a Wafer. Reponer si reaparece la fuente.

### Radar de modelos locales (Modo 1) — el chat del ThinkPad 8 GB

Campo local de 8 GB **investigado entero (2026-08-04)**. Conclusión: **NO hay candidato oculto
mejor** — los nombres se repiten (Qwen, Gemma, Phi, Llama, Mistral); no hay un "tapado" fuera de
esa lista. Deja de tener sentido seguir barriendo el campo.

- **Regla física confirmada:** en **CPU 8 GB, la zona real es 3-4B**. Un 8B **cabe** (~5.5 GB en
  Q4) pero **va lento en CPU sin GPU** → **8B DESCARTADO para el ThinkPad**. La restricción no es
  la RAM, es el throughput sin GPU.
- **Los dos candidatos siguen siendo `Qwen 3.6 4B` y `Gemma 4 4B`** — confirmados ahora contra el
  campo entero, no elegidos a ciegas.
- **NUEVO al radar — `Phi-4 Mini`** (Microsoft, 3.8B, **el más rápido, ~28 tok/s**): fuerte en
  **inglés/código**, pero **DÉBIL en multilingüe europeo** → **candidato débil para MI criterio
  (catalán)**. Anotado y **casi descartado** (la velocidad no compensa el idioma).
- **El desempate sigue siendo EMPÍRICO:** **ningún benchmark mide catalán** (miden swahili,
  bengalí…). La **única prueba válida** es **bajar Qwen 4B + Gemma 4B y probar catalán a mano**.
  **No leer más benchmarks** — no van a resolver esto. Liga con el swap ca/pl del frente de voz
  (`docs/Vokter_Voz.md`): catalán = candidato #1 a recuperar cuando el chat lo aguante.

---

## Proyectos externos analizados (radar)

Proyectos-agente concretos. Cada entrada lleva veredicto explícito: **cantera** (adoptar código
o depender) vs **radar** (se observa, no se toma). Criterio de fondo: soberanía — local-first,
sin fugar contexto a la nube, licencia libre.

### Panda / blurr — github.com/Ayush0Chaudhary/blurr (Kotlin, licencia Personal-Use, 928★)

Agente Android que controla el teléfono tocando la UI como un humano (Accessibility
Service: lee el árbol de elementos de pantalla + simula toques/swipes/texto). Brain =
Gemini (nube), voz = Google Cloud Chirp, memoria local DESACTIVADA en el README.

**Veredicto: radar, NO cantera.** Anti-patrón de soberanía — todo el contexto sale a
Google, voz y búsqueda cloud, licencia no libre. NO adoptar ni depender.

**Único aprovechable (concepto, no código):** el paradigma "Eyes & Hands" — actuar sobre
apps que NO exponen API leyendo el árbol de accesibilidad + simulando input. Vokter hoy
actúa solo por canales estructurados (A2A / MCP / browse). Referencia para el día
(lejano) en que Vokter quiera operar UIs de escritorio legadas sin API. Equivalente Linux:
AT-SPI (árbol de accesibilidad GNOME) o xdotool/ydotool. NO construir ahora — precedente
anotado.

**No tomar:** Tavily Search (búsqueda cloud, la query sale fuera), Gemini-por-defecto, voz
Chirp (cloud), el código (Kotlin/Android + licencia Personal-Use).

**Observación que refuerza la tesis de Vokter:** Panda desactivó su memoria local y tiró de
nube para todo. Ejemplo vivo de lo difícil que es lo local-first bien hecho — y de por qué
la memoria con gates de Vokter es un foso que un agente-en-la-nube no tiene.
