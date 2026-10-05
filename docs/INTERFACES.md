# Interfaces entre módulos (para construção em paralelo)

Cada módulo de comando expõe `add_parser(sub)` e registra `func=<fn>(args, config) -> int` (ver `ata/cli.py`,
`COMMAND_MODULES`). Códigos de saída em CONTRATO §6. Mensagens ao usuário em pt-BR. Nada de texto de reunião
em logs. Tudo testável com `ATA_ENGINES=fake` (o `tests/conftest.py` já força isso e isola HOME/config).

Núcleo pronto (não reescrever; pode acrescentar funções pequenas se faltar algo, sem quebrar as existentes):
`ata.types`, `ata.i18n`, `ata.config`, `ata.audio`, `ata.bundle`, `ata.engines.base`, `ata.engines.fake`,
`ata.engines.registry`, `ata.testing` (reunião sintética), `ata.cli`.

## A — Gravação (`ata.capture.*`, `ata.recorder`)
- `ata.capture.base`: `class CaptureBackend(Protocol)`: `name`, `devices() -> dict`, `start(bundle_dir, far, mic) -> Handle`,
  `Handle.stop() -> dict[track, Track]`. `backend_for_platform(platform=sys.platform)`.
- `ata.capture.linux` (PipeWire: 2× `pw-record`, `stream.capture.sink=true`; `start_epoch` estimado pelo 1º som
  e corrigido por `audio.estimate_lag` quando há vazamento; `start_measured=False` -> `start_unmeasured`),
  `ata.capture.windows` (PyAudioWPatch loopback + mic, mesmo relógio), `ata.capture.macos` (protocolo do helper
  Swift: eventos JSON por linha; fonte Swift em `helpers/macos/`), `ata.capture.fake` (gera áudio sintético; para testes).
- `ata.recorder`: comandos `start [--title] [--speakers N] [--lang] [--no-process]`, `stop [--no-process]`, `toggle`,
  `status [--json]`. API Python usada por MCP/dashboard/live:
  - `start(config, *, title=None, speakers=None, language=None) -> Path` (bundle_dir; supervisor destacado)
  - `stop(config, *, process=True) -> dict` (`{"bundle": str, "note": str|None}`)
  - `status(config) -> dict` (`{"phase": "idle"|"recording", "bundle", "started_at", "elapsed_s", "pid"}`)
  - estado em `<cache>/state.json`, lock com PID; arquivo `STOP` no bundle pede parada.

## B — Pipeline e nota (`ata.pipeline.*`, `ata.note`, `ata.compat.crunchlog`)
- `ata.pipeline.run.process_bundle(bundle_dir, config, *, language=None, speakers=None, summarize=True, log=None) -> Path`
  roda: ler meta (+compat) -> ASR por faixa (motor do idioma; fallback se EngineError) -> offsets (único ponto)
  -> gate (tira do mic as palavras que eram eco do far) -> diarização do far (+ mic se `mic_speakers>1`) ->
  rótulos (Eu / Pessoa N, por idioma) -> voiceprints `ata.voices.auto_label(...)` se habilitado -> turnos ->
  limpeza (fillers por idioma + glossário) -> `words.json`, `turns.json` -> resumo `ata.knowledge.summary.summarize_turns(...)`
  -> `note.md` (no bundle e cópia em `paths.notes/<nome>.md`) -> `done.json` -> `ata.knowledge.index.index_bundle(...)`
  -> `ata.connect.after_note(...)`. Devolve o caminho da nota em `paths.notes`. Escreve `meta.engines`.
- `ata.pipeline.gate.gate_mic_words(mic_words, far_audio, mic_audio, margin_db) -> (kept, dropped)` (puro, testável).
- `ata.pipeline.turns.build_turns(far_words, far_spans, mic_words, labels, language) -> list[Turn]`.
- `ata.pipeline.cleanup.clean_text(text, language, glossary) -> str`.
- `ata.note.render_note(meta, turns, summary, names, my_notes, damage, language) -> str` — frontmatter YAML
  (`date, start, duration, language, speakers, participants, bundle, engines, tags: [reuniao]`), seções
  traduzidas (`i18n.TEXTS`), linhas `[mm:ss] Falante: texto`.
- `ata.compat.crunchlog.meta_from_crunchlog(d) -> BundleMeta` (lê `bundle_version: 2`: tracks far/mic com
  `start_epoch`, `sample_rate`, `samples`, `silent`; idioma "en"; `source_schema="crunchlog/2"`).
- Comandos: `process <alvo> [--lang] [--speakers N] [--dry-run] [--no-summary]` (dry-run imprime
  `bundle crunchlog/2 lido: 2 tracks, offsets=...` ou `bundle ata/1 lido: ...`), `import <arquivo> [--lang] [--title]`
  (wav direto; outros via ffmpeg; vira só far), `export <alvo> --format srt|vtt|txt|json|md|csv [--out]`,
  `rerender <alvo>`, `speakers <alvo> ["Pessoa 2=Ana" ...]` -> todos em `ata.pipeline.commands`.

## C — Motores reais e operação (`ata.engines.{nemo,onnx,ollama,cli_llm}`, `ata.engine_cmd`, `ata.doctor`, `ata.bench`, `ata.setup`)
- Fábricas referenciadas em `registry._FACTORIES` (`make_asr(config, model)`, etc.).
- `nemo`: cliente do sidecar NeMo-Speech.cpp (`nemo-speech serve`) em `engine.host:port`: HTTP
  `POST /v1/audio/transcriptions` (multipart, `word_timestamps`, `language`, `diarize`) e WebSocket
  `/v1/audio/transcriptions/realtime` para streaming. Só stdlib (`urllib`) para HTTP; `websockets` (extra live) para WS.
  Testar contra um servidor FAKE local (thread `http.server`) que devolve fixtures.
- `onnx`: onnx-asr + sherpa-onnx importados preguiçosamente; sem pacote -> `EngineMissing` com a linha de instalação.
- `ollama`: `POST /api/chat` com `format=<schema>` e `/api/embed` (urllib). Testar com servidor fake.
- `cli_llm`: `claude -p --output-format json` / `codex exec` via subprocess com ferramentas desligadas, cwd vazio,
  timeout; binário injetável para teste (`fake-claude` script). Nunca API.
- `ata.engine_cmd`: `engine install [--backend auto|cuda13|cuda12|cpu|metal|vulkan] [--dry-run]`, `engine start|stop|status`
  (start via `systemd-run --user --scope -p MemoryMax=` no Linux; PID em `<cache>/engine.json`). Install baixa o
  release do NeMo-Speech.cpp e modelos com sha256 de um manifesto `ata/data/models.toml`; `--dry-run` só imprime.
- `ata.doctor`: comando `doctor [--json] [--skip-benchmark]` (plataforma, PipeWire/pw-record, config, pastas,
  sidecar, onnx, GPU `nvidia-smi`, ollama, claude/codex no PATH, ffmpeg, disco) e `privacy [<bundle>]`.
- `ata.bench`: `bench asr|diar|summary --set <pasta com wav+txt/rttm> --engines a,b` -> WER (implementação própria
  de distância de edição com normalização por idioma) e DER simples; JSON em `bench/results/`.
- `ata.setup`: `setup [--yes] [--lang] [--notes] [--recordings] [--summary none|ollama|claude|codex]` escreve config.

## D — Conhecimento (`ata.knowledge.*`, `ata.voices`, `ata.connect`)
- `ata.knowledge.summary`: `SUMMARY_SCHEMA` (CONTRATO §4), `build_prompt(turns, language, my_notes, template)`
  (inclui `LANGUAGE: <lang>` e linhas `[mm:ss] Falante: texto`), `validate(obj) -> list[str]`,
  `summarize_turns(turns, language, config, *, my_notes=None) -> dict|None` (None se provider none; erro do
  provedor -> devolve None e loga, nunca derruba o pipeline).
- `ata.knowledge.index`: SQLite `<cache>/ata.db` com FTS5 (+ vetores em tabela própria, cosseno em numpy):
  `index_bundle(bundle_dir, config)`, `reindex(config)`, `search(config, query, mode="hybrid", limit=10, filters) -> list[Hit]`
  (`Hit`: meeting, title, date, start, speaker, text, score, note_path). Fusão RRF k=60.
- `ata.knowledge.ask.ask(config, question, *, meeting=None) -> dict` (evidências + resposta do summarizer; citações
  `[título, mm:ss]`); grava em `ask.md` do bundle quando `meeting` dado.
- `ata.knowledge.prep.build_prep(config, query, days=90) -> str` (Markdown: ações abertas, decisões, perguntas
  das notas que casam, roteiro com `(qN)`), `tick_prep(...)` marca itens respondidos.
- `ata.knowledge.actions`: ações/decisões entre reuniões a partir de `summary.json`.
- Comandos em `ata.knowledge.commands`: `search`, `ask`, `prep`, `actions [--decisions]`, `reindex`.
- `ata.voices`: opt-in (`voices.enabled`); `<cache>/voices.json`; `auto_label(bundle_dir, config, spans_by_track, labels) -> dict[label,name]`;
  comandos `voices enroll <nome> <alvo> <rótulo>`, `voices list`, `voices forget [<nome>|--all]`.
- `ata.connect`: `after_note(bundle_dir, note_path, config)` (copia/sincroniza conforme conexões ativas em
  `<cache>/connections.json`); comando `connect cerebro --dir P | obsidian --vault P | agentic-os [--vault P]`
  com `--dry-run`, `--undo`, `--status`. Cérebro: escreve `fontes/AAAA-MM-DD-slug.md` (frontmatter `tipo: fonte`,
  `subtipo: reuniao`) e `decisoes/registro.md` (append). agentic-os: só escreve num vault e imprime a linha
  `claude mcp add --scope user ata -- ata mcp` (não executa sem `--register`).

## E — Interfaces (`ata.mcp_server`, `ata.dashboard`, `ata.live`, `ata.demo`, `skills/`, plugin)
- `ata.mcp_server`: comando `mcp` (stdio; `--http` loopback). Ferramentas `ata_*` (≥ 18): status, list, read,
  search, ask, actions, decisions, prep, person, record_start, record_stop, record_toggle, process, job,
  rename_speakers, export, reindex, doctor. Implementação com SDK `mcp` (FastMCP) se instalado; o teste usa stdio.
- `ata.dashboard`: comando `dashboard [--port] [--no-browser]`, servidor `http.server` em 127.0.0.1, token na
  1ª visita (cookie), API JSON (`/api/meetings`, `/api/meetings/<id>`, `/api/status`, `/api/record/start|stop`,
  `/api/search?q=`, `/api/speakers` POST, `/api/events` SSE), página única em `ata/dashboard/static/` (PT/EN/ES).
- `ata.live`: comando `live start|tail|stop` — lê `far.wav`/`mic.wav` crescendo no bundle em gravação, manda PCM
  ao `StreamingAsr`, grava `live/turns.jsonl`, marca itens `(qN)` da cola em `live/prep.json`.
- `ata.demo`: comando `demo [--lang pt-BR|en|es] [--no-tts] [--no-process]`. Roteiros PRÓPRIOS nos 3 idiomas
  (reunião inventada, ~25 falas, fatos verificáveis, 1 sobreposição). Com TTS: Piper (`piper` no PATH, vozes por
  idioma em `<cache>/piper-voices`); `--no-tts` usa `ata.testing.synth_meeting` + fixtures fake. Última linha
  `note: <caminho>`.
- `skills/` (Claude Code/Codex): `meeting-notes`, `action-items`, `meeting-prep`, `ask-meetings`, `meeting-to-wiki`
  (SKILL.md cada) + `.claude-plugin/plugin.json` + `.mcp.json`.
