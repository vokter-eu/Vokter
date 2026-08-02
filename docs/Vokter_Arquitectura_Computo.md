# Vokter — Arquitectura de Cómputo

Documento vivo. Decisiones y observaciones sobre el modelo de cómputo de Vokter
(local-first, soberano) y el seguimiento de proyectos externos del ecosistema.

---

## Proyectos externos analizados (radar)

Seguimiento de agentes/proyectos afines. Cada entrada lleva un veredicto explícito:
**cantera** (algo que adoptar o de lo que depender) vs **radar** (se observa, no se toma).
El criterio de fondo es la soberanía: local-first, sin fugar contexto a la nube, licencia
libre.

### Panda / blurr — github.com/Ayush0Chaudhary/blurr (Kotlin, licencia Personal-Use, 928★)

Agente Android que controla el teléfono tocando la UI como un humano (Accessibility
Service: lee el árbol de elementos de pantalla + simula toques/swipes/texto). Brain =
Gemini (nube), voz = Google Cloud Chirp, memoria local DESACTIVADA en el README.

**Veredicto: radar, NO cantera.** Anti-patrón de soberanía — todo el contexto sale a
Google, voz y búsqueda cloud, licencia no libre. NO adoptar ni depender.

**Único aprovechable (concepto, no código):** el paradigma "Eyes & Hands" — actuar sobre
apps que NO exponen API leyendo el árbol de accesibilidad + simulando input. Vokter hoy
actúa solo por canales estructurados (A2A / MCP / browse / wallet). Referencia para el día
(lejano) en que Vokter quiera operar UIs de escritorio legadas sin API. Equivalente Linux:
AT-SPI (árbol de accesibilidad GNOME) o xdotool/ydotool. NO construir ahora — precedente
anotado.

**No tomar:** Tavily Search (búsqueda cloud, la query sale fuera), Gemini-por-defecto, voz
Chirp (cloud), el código (Kotlin/Android + licencia Personal-Use).

**Observación que refuerza la tesis de Vokter:** Panda desactivó su memoria local y tiró de
nube para todo. Ejemplo vivo de lo difícil que es lo local-first bien hecho — y de por qué
la memoria con gates de Vokter es un foso que un agente-en-la-nube no tiene.
