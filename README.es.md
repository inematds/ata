# Ata — notas de reunión que no salen de tu máquina

[![Ata: tu reunión en acta](guia/assets/banner-es.jpg)](https://inematds.github.io/ata/guia/es/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

## 📖 Guía de uso

Guía completa (landing + paso a paso): **https://inematds.github.io/ata/guia/es/**

---

**Ata** graba tu reunión en **dos pistas**: lo que escuchas (Meet, Zoom, Teams, YouTube) y lo que dices. Después transcribe en tu máquina, en **español, portugués o inglés**, y entrega una nota en Markdown con cada intervención marcada por persona. También genera resumen, decisiones y acciones con responsable. No entra ningún bot a la llamada, no necesita cuenta y el audio nunca sale del computador.

- **Licencia:** MIT. Proyecto abierto de INEMA.
- **Inspiración:** las ideas de [CrunchLog](https://www.skool.com/aiautomationsbyjack), de Christian Landsteiner (dos pistas, "el micrófono eres tú", la grabación como fuente de verdad). Ata es una **implementación independiente**: no se usó ninguna línea de código de CrunchLog.
- **Estado:** 0.1.0. Todas las fases están implementadas y probadas con motores simulados. Los motores reales (NeMo-Speech.cpp en la GPU, onnx en la CPU, Ollama) están implementados, pero **aún no se midieron en esta versión** (ver [Estado y límites](#estado-y-límites)).

## Qué hace

| Función | Cómo |
|---|---|
| **Dos pistas, sin driver** | Linux: PipeWire (`pw-record`). Windows: WASAPI loopback. macOS: helper Swift con *process tap* (14.2+, código fuente en `helpers/macos/`) |
| **Micrófono = tú** | Todo lo que viene del micrófono es "Yo". El *gate* quita del micrófono el eco del parlante, y la separación de hablantes solo actúa del otro lado |
| **pt-BR, EN, ES** | Motor, limpieza de muletillas ("né", "tipo"; "um", "uh"; "este", "o sea"), nota, resumen, demo y preparación, todo por idioma |
| **GPU o CPU** | Motor principal: NeMo-Speech.cpp (Parakeet v3 + Nemotron, CUDA en el DGX Spark/GB10, Metal en Mac, CPU en los demás). Respaldo en proceso: onnx-asr + sherpa-onnx |
| **Resumen local** | Ollama por defecto (JSON con resumen, temas, decisiones, acciones con responsable y plazo, preguntas). Opcional: `claude` o `codex` por tu suscripción |
| **Búsqueda y preguntas** | Índice SQLite (FTS5 + vectores) en todas las reuniones. `ata ask` responde con cita `[reunión, mm:ss]` |
| **Preparación** | `ata prep "cliente"` arma la agenda con acciones abiertas, decisiones y preguntas; los ítems se marcan solos en la reunión siguiente |
| **Reconocer voces** | Opcional y desactivado por defecto: registra la voz de una persona y "Persona 2" pasa a ser "Ana" en las próximas reuniones |
| **Segundo cerebro** | `ata connect cerebro --dir ...` escribe la reunión en `fontes/` y las decisiones en `decisoes/registro.md`. También hay Obsidian y Agentic OS |
| **Modo en vivo** | Transcripción local durante la llamada (Nemotron 3.5 streaming), con la agenda marcándose sola, sin servicio de pago |
| **Agentes** | Servidor MCP (`ata mcp`), skills y plugin de Claude Code |
| **Dashboard** | `ata dashboard`: grabar, escuchar, transcripción clicable, renombrar hablantes, búsqueda y modo en vivo (PT/EN/ES) |
| **Compatible con CrunchLog** | Lee grabaciones de CrunchLog (`bundle_version: 2`) sin alterar los originales |

## Instalar

```bash
# 1. uv (gestor de Python)
curl -LsSf https://astral.sh/uv/install.sh | sh                      # Linux/macOS
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows

# 2. Ata (con MCP, modo en vivo y motor CPU)
git clone https://github.com/inematds/ata && cd ata
uv tool install --python 3.12 ".[mcp,live,onnx]"

# 3. Configurar (idioma, carpetas, resumen)
ata setup --lang pt-BR --summary ollama

# 4. Motor de transcripción (detecta la GPU: cuda13 en el GB10, metal en Mac, cpu en el resto)
ata engine install --dry-run            # muestra lo que va a descargar
ata engine install --allow-unverified   # en esta versión los hashes aún no están fijados
ata engine start

# 5. Verificar
ata doctor
```

## Usar

```bash
ata demo --lang pt-BR                  # reunión inventada → nota (con Piper; --no-tts sin voz)
ata toggle                             # empieza a grabar; de nuevo se detiene y genera la nota
ata start --title "Kickoff Acme" --speakers 3 --lang pt-BR
ata stop
ata import gravacao.mp4 --lang es      # audio/video que ya tienes
ata process <grabación> --speakers 4   # reprocesar
ata speakers <grabación> "Persona 2=Ana" "Persona 3=Bruno"
ata export <grabación> --format srt    # srt|vtt|txt|json|md|csv
ata search "plazo del beta" --since 30d
ata ask "¿qué decidimos sobre el lanzamiento?"
ata actions --owner Ana
ata prep "acme" --calendar "Jue 14h, Ana y Bruno"
ata voices enroll Ana <grabación> "Persona 2"   # opcional (requiere voices.enabled = true)
ata connect cerebro --dir ~/mi-cerebro
ata live start                         # modo en vivo durante la grabación
ata dashboard                          # http://127.0.0.1:47530/
ata mcp                                # servidor MCP para Claude Code / Codex
ata privacy <grabación>                # qué motores y destinos tocaron esta reunión
ata bench asr --set <carpeta> --engines nemo,onnx --lang pt-BR   # WER y velocidad
```

Atajo de teclado: asigna `ata toggle` a una tecla (GNOME/KDE, PowerToys/AutoHotkey, Atajos de macOS o Stream Deck).

**MCP en Claude Code:** `claude mcp add --scope user ata -- ata mcp`. El plugin con las skills (`meeting-notes`, `action-items`, `meeting-prep`, `ask-meetings`, `meeting-to-wiki`) está en `.claude-plugin/` y `skills/`.

## Cómo funciona

```
tecla / dashboard / MCP
   │
   ▼  grabación (PipeWire · WASAPI · helper macOS) → carpeta de la reunión (bundle ata/1)
far.wav (lo que escuchas) + mic.wav (tú) + meta.json (reloj, idioma, motores)
   │
   ▼  ata process (todo local)
ASR por pista e idioma → un único punto de alineación de tiempo → el gate quita el eco del mic
→ separación de hablantes del far (+ mic opcional) → voiceprints (opcional) → turnos
→ limpieza por idioma + glosario → resumen JSON (Ollama | claude | codex)
→ nota.md (bundle + carpeta de notas) → índice de búsqueda → segundo cerebro / Obsidian
```

Contrato completo: [`docs/CONTRATO.md`](docs/CONTRATO.md). Interfaces entre módulos: [`docs/INTERFACES.md`](docs/INTERFACES.md).

## Privacidad

| Nivel | Qué sale de la máquina |
|---|---|
| **0 (por defecto)** | Nada. Transcripción, separación de hablantes, resumen (Ollama), búsqueda y modo en vivo corren todos en local |
| **1 (suscripción)** | Solo texto de la transcripción, nunca audio, hacia Anthropic u OpenAI vía `claude -p` o `codex exec`, con las herramientas desactivadas. Requiere `privacy.level = 1` |

Ningún módulo llama a una API de pago. Los logs y errores nunca contienen texto de reuniones. Las voiceprints quedan solo en tu máquina y se pueden borrar (`ata voices forget --all`).

## Estado y límites

- **Pruebas:** suite con más de 400 pruebas, sin red y sin modelos (motores simulados). La grabación real por PipeWire se probó en Linux.
- **Aún no medido en esta versión:** rendimiento y calidad de los motores reales (NeMo-Speech.cpp, onnx, Ollama). La API de NeMo-Speech.cpp sigue lo documentado en `src/ata/engines/nemo.py`. Los hashes de verificación de las descargas están vacíos en `src/ata/data/models.toml`, por eso la instalación exige `--allow-unverified`.
- **macOS:** el helper Swift existe en código fuente; aún no se compiló ni se probó.
- **Windows:** captura implementada; no probada en una máquina real en esta versión.

## Desarrollo

```bash
uv sync --all-groups
uv run pytest -q                       # suite estándar (sin red, sin modelos)
uv run pytest -q -m linux_audio        # grabación real vía PipeWire
```

## Créditos

Inspirado en las ideas de CrunchLog (Christian Landsteiner). Modelos: NVIDIA Parakeet / Nemotron (NeMo-Speech.cpp), pyannote y WeSpeaker vía sherpa-onnx, Ollama. Investigación y propuesta de arquitectura: `docs/` y el proyecto `crunchlog` de INEMA.
