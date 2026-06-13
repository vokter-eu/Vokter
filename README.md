# Vokter

**Tu agente. Tus datos. Tu dinero. Por derecho.**

Vokter (noruego: *guardián*) es un agente de IA personal y soberano que vive en **tu** máquina. Sin nube de terceros, sin cuentas, sin telemetría. Solo sabe lo que tú le enseñas, y puedes auditar cada línea de código que lo hace posible.

> Hay un derecho más viejo que internet: lo que es tuyo no se toca. Los noruegos lo llaman *odel*. Vokter es su guardián digital.
> — [Lee el manifiesto completo](docs/MANIFIESTO.md)

## Estado del proyecto

🚧 **Fase 0 — Esqueleto.** Esto es el día 1. Si has llegado hasta aquí, eres de los primeros. Estrella el repo y vuelve pronto, o mejor: [contribuye](CONTRIBUTING.md).

## Hoja de ruta

- [ ] **Fase 1 — Tu agente en tu máquina**: LLM local (Ollama), memoria personal cifrada, chat con tus documentos. 100% offline.
- [ ] **Fase 2 — Tu agente sale al mundo**: navegación web con permisos granulares, planificación de tareas reales (viajes, compras, gestiones) y **voz 100% local** (oído con Whisper, voz con Piper — hablas con Vokter sin que tu voz salga de casa). Propone; tú decides.
- [ ] **Fase 3 — Tu agente paga**: monedero no-custodio con **arquitectura modular agnóstica del activo** — por defecto, stablecoins reguladas bajo MiCA (EMTs autorizados); cualquier otro activo (BTC, ETH…) como adaptador opcional enchufable, sin tocar el núcleo. Confirmación humana obligatoria y límites de gasto siempre.

## Arranque rápido (v0.1)

Requisitos: Docker y Docker Compose. Recomendado: 16 GB de RAM (con 8 GB, cambia el modelo a `llama3.2:3b` en el compose).

```bash
git clone https://github.com/vokter/vokter.git
cd vokter/docker
docker compose up -d --build
docker exec -it vokter-ollama ollama pull llama3.1:8b
docker exec -it vokter-ollama ollama pull nomic-embed-text
```

Abre **http://localhost:8080**: sube un PDF y pregúntale lo que quieras. Ni un byte ha salido de tu máquina — compruébalo tú mismo: ese es el punto.

Lo que ya hace la v0.1: ingesta de PDF/TXT/MD, memoria local en SQLite, respuestas basadas solo en tus documentos con citación de fuentes, panel "qué sabe Vokter" y borrado real (documento + embeddings). Pendiente para v0.2, dicho con honestidad: cifrado en reposo de la base de datos y conector de email.

## Principios innegociables

1. **Local primero.** Por defecto, todo se procesa en tu hardware.
2. **Cero llamadas ocultas.** Ninguna petición a APIs de IA de terceros. Verificado en CI.
3. **Tus claves, tu dinero.** Cuando lleguen los pagos: no-custodio o nada.
4. **Borrado real.** Eliminar significa eliminar, incluidos los embeddings.
5. **Código abierto.** No pedimos confianza; damos pruebas.
6. **Para tu vida, no para retenerte.** Vokter conoce tu mundo para devolverte tiempo y empujarte hacia tu vida real — jamás usará mecánicas de apego, soledad o enganche.

## Licencia

AGPL-3.0 — libre para siempre, y las mejoras vuelven a la comunidad.

## Comunidad

- Web: [vokter.eu](https://vokter.eu) *(próximamente)*
- Discusiones: pestaña Discussions de este repositorio

---

*Vokter es un proyecto independiente europeo. No está afiliado a ninguna big tech, y esa es exactamente la idea.*
