# Proposta: um notetaker local-first superior ao CrunchLog

Data: 05/10/2026. Base: [PESQUISA-ESTADO-DA-ARTE.md](PESQUISA-ESTADO-DA-ARTE.md) (componentes) e [ANALISE-CRUNCHLOG.md](ANALISE-CRUNCHLOG.md) (o que o CrunchLog faz e onde para). Host principal: spark-922b (NVIDIA GB10, aarch64, Ubuntu 24.04, PipeWire, 128 GB unificados). Projeto INEMA: aberto, gratuito, educacional.

---

## 0. Fork vs. recriar do zero

| Critério | Fork do CrunchLog (evoluir o código dele) | Recriar do zero (implementação independente, inspirada nas ideias) |
|---|---|---|
| **Licença / publicação** | "Evaluation Licence": modificar para uso próprio é permitido, **redistribuir é proibido** (cláusula 3). O fork fica em repo **privado** para sempre, salvo permissão escrita do autor. Não pode ir ao portal INEMA como código, nem receber contribuição externa. | Repo **público** (MIT), no padrão INEMA (`inematds/<nome>`, `guia/`, card no portal). Ideias e arquitetura não são protegidas; só o código e os textos dele. |
| **Qualidade de partida** | Alta: ~62 k linhas, 1 621 testes, captura Windows/mac pronta, helper Swift, dashboard, MCP, plugin, docs. | Zero código, mas a análise (ANALISE §9) já lista 16 ideias testadas para reaproveitar e os erros a não repetir (§10). |
| **Velocidade até um produto usável** | Dias: já roda aqui com nossos scripts Linux. | Semanas: MVP Linux em ~4 semanas de build por agente; Windows na 3ª fase; macOS na 4ª. |
| **Merges com o upstream** | Alpha privado de um autor só (2.0.0a1 saiu em 04/10/2026): churn alto; nossas mudanças (idioma, Linux, GPU, live local, busca) tocam `config.py`, `note.py`, `prep.py`, `cleanup.py`, `live/*`, `pipeline/run.py` → rebases dolorosos. | Não existe upstream; o CrunchLog instalado vira **oráculo** de testes diferenciais (mesmo bundle → comparar turnos). |
| **pt-BR / Linux / GPU-first** | Tudo é retrofit: inglês hard-coded em 10+ pontos, "sem torch e sem GPU" é decisão de arquitetura (ADR 0003), dashboard stdlib sem WebSocket. | Projetado assim desde o `meta.json` (campo `language`), motor único NeMo-Speech.cpp (ASR pt-BR + diarização + VAD + pontuação, CPU/CUDA/Metal nos 3 SOs), live local nativo. |
| **Risco** | Legal (publicar por engano), dependência de um autor, código que não conhecemos a fundo. | Clean-room (disciplina para não copiar), esforço do helper macOS, menos testes no início. |
| **Encaixe com o INEMA** | Ferramenta privada do Nei. | Projeto publicável, com guia trilíngue e curso derivado. |

**Recomendação: recriar do zero**, como implementação independente inspirada nas ideias do CrunchLog (duas faixas, mic = Eu, gate, bundle como fonte de verdade, supervisor com STOP/locks, rerender, doctor com benchmark, MCP como cliente do CLI, demo com gabarito, resumo pela assinatura). Motivos decisivos: (1) o produto precisa ser **público** para ser um produto INEMA; (2) pt-BR/Linux/GPU são a espinha, não um remendo; (3) o motor que a pesquisa apontou (NeMo-Speech.cpp + Nemotron 3.5/3) muda a arquitetura de engines de qualquer jeito; (4) o CrunchLog continua útil **como está**: ferramenta do dia a dia durante a construção e oráculo para testes diferenciais (uso permitido pela licença).

Se o autor conceder permissão escrita no futuro, nada impede importar partes dele; a proposta não depende disso.

**Disciplina clean-room (obrigatória):** quem implementa trabalha a partir de PESQUISA + ANALISE + esta proposta, **não do código-fonte** em `_fonte/`; nenhuma linha, nome de função ou texto de prompt/skill é copiado; o layout de arquivos do bundle é reimplementado a partir da descrição (formato de dados não é código); o README credita "inspirado nas ideias do CrunchLog, de Christian Landsteiner" e nada mais do CrunchLog é redistribuído (nem o zip, nem docs, nem o helper mac).

---

## 1. Nome (3 sugestões)

| Nome | CLI / repo | Prós | Contras |
|---|---|---|---|
| **Ata** (recomendado) | `ata`, `inematds/ata`, `ata.inema.club` | "Ata" = registro oficial de reunião em pt-BR; 3 letras; `ata start`, `ata ask`, `ata.db`; soa bem em ES ("acta") | genérico demais para busca na web (usar "Ata INEMA") |
| **Pauta** | `pauta` | cobre antes (pauta) e depois (ata); palavra viva em pt-BR | sugere agenda mais do que transcrição |
| **Escriba** | `escriba` | persona (quem escreve por você); igual em ES, legível em EN | 7 letras; conota serviço humano |

O restante do documento usa **Ata**.

---

## 2. Para quem

1. **Nei / INEMA** (uso diário, host GB10, Linux): reuniões de projeto, mentorias, lives, entrevistas; notas no segundo cérebro (`astra-2cerebro`) e consulta via Claude Code/Codex.
2. **Alunos e seguidores do INEMA** (Windows e macOS, sem GPU): instalar com um comando, gravar Meet/Zoom/Teams sem bot, nota em pt-BR, tudo local.
3. **Pesquisadores / educadores** que precisam de transcrição com quem-falou-quando e podem citar `[reunião, mm:ss]`.
4. **Agentes** (Claude Code, Codex, n8n): o produto é também uma API local de memória de reuniões.

---

## 3. Princípios

1. **Local por padrão, verificável**: áudio nunca sai da máquina; texto só sai se o usuário ligar um provedor por assinatura; `ata doctor --privacy` diz o que sai.
2. **Mic = Eu por construção**; diarização só onde há incerteza.
3. **Bundle é a fonte de verdade**; nota, resumo, índice e exportações são derivados e recriáveis (`ata rerender`, `ata reindex`).
4. **pt-BR, EN e ES de primeira classe**: motor, fillers, templates, nota, demo, prep e live escolhidos por idioma (`language` no bundle).
5. **Um relógio por gravação**: duas faixas alinhadas com `start_epoch` medido no áudio, não no `Popen`.
6. **GPU quando houver, CPU sempre**: mesmo contrato de engine; `doctor` mede e escolhe.
7. **Nada de API paga sem autorização explícita** (regra do projeto); Ollama e CLIs por assinatura são os caminhos.
8. **Agente-first**: MCP, skills e Markdown são interfaces, não extras.
9. **Falhas visíveis, nunca silenciosas**: `damage_report`, exit codes semânticos, logs sem texto de reunião.
10. **Medir antes de escolher**: `ata bench` é parte do produto.

---

## 4. Arquitetura

```
 ┌───────────────────────────────── ata (Python 3.12, uv) ─────────────────────────────────┐
 │                                                                                          │
 │  CLI `ata`  ──┐                                                                           │
 │  MCP (stdio/HTTP loopback) ──┤   todos são CLIENTES do núcleo; nada reimplementado         │
 │  Dashboard (loopback, SSE) ──┤                                                            │
 │  Skills Claude Code / Codex ─┘                                                            │
 │                 │                                                                        │
 │                 ▼                                                                        │
 │  ┌──────── núcleo ────────┐     ┌──────── pipeline ─────────┐    ┌──── conhecimento ────┐ │
 │  │ supervisor (destacado) │     │ asr por faixa (por idioma)│    │ notas Markdown        │ │
 │  │ state / STOP / locks   │ ──▶ │ gate (puro)               │ ─▶ │ ata.db (FTS5+vec)     │ │
 │  │ bundle (meta, tracks,  │     │ diarização far [+ mic]    │    │ voiceprints (opt-in)  │ │
 │  │  language, damage)     │     │ reid por voiceprint       │    │ ações / decisões      │ │
 │  │ capture backends       │     │ turns + smoothing         │    │ prep semântico        │ │
 │  └───────┬────────────────┘     │ resumo (schema JSON)      │    │ sync 2º cérebro       │ │
 │          │                      └──────────┬────────────────┘    └──────────────────────┘ │
 │          ▼                                 ▼                                            │
 │  ┌─ captura por SO ─────────┐   ┌─ engines (protocolos AsrEngine/Diarizer/Summarizer) ─┐ │
 │  │ linux: pw-record×2 (MVP) │   │ sidecar: nemo-speech serve (HTTP+WS, 127.0.0.1)       │ │
 │  │   → helper Rust (v2)     │   │   ASR offline+streaming, VAD, pontuação, diarização   │ │
 │  │ windows: WASAPI loopback │   │   CPU / CUDA (GB10) / Metal / Vulkan                  │ │
 │  │ macos: helper Swift tap  │   │ fallback in-process: onnx-asr (Parakeet v3/v2 int8)   │ │
 │  │ live: 2º consumidor PCM  │   │   + sherpa-onnx (seg-3.0 + WeSpeaker), sem torch      │ │
 │  └──────────────────────────┘   │ resumo: Ollama (padrão) | claude -p | codex exec      │ │
 │                                 │ embeddings: Ollama (Qwen3-Embedding-0.6B | bge-m3)    │ │
 │                                 └──────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
   Bundle: <recordings>/AAAA-MM-DD-HHMM-slug/{far.wav, mic.wav, meta.json, words.json,
           turns.json, summary.json, note.md, live/, done.json}   ← fonte de verdade
```

Camadas e contratos (documentados em `docs/CONTRATO.md` do novo repo, com docstrings-contrato como no CrunchLog):

- **Bundle `ata/1`**: mesmo espírito do bundle v2 do CrunchLog, com acréscimos: `language` (`pt-BR|en|es|auto` + detectado), `engines` (asr/diar/summary usados e versões), `tracks[].start_epoch` **medido no áudio**, `damage_reasons` com vocabulário fechado. Um **leitor de compatibilidade** processa bundles `bundle_version: 2` do CrunchLog (só leitura do formato) — serve para testes diferenciais e para migrar gravações antigas.
- **Engines por protocolo**: `AsrEngine.transcribe(wav, language) -> words[file time]`, `Diarizer.diarize(wav, max_speakers) -> spans`, `Summarizer.summarize(payload, schema) -> json`, `Embedder.embed(texts)`. Implementações: `nemo_speech` (sidecar HTTP/WS), `onnx` (onnx-asr + sherpa-onnx), `ollama`, `claude_cli`, `codex_cli`. Seleção por config e por idioma: `[asr] pt-BR = "nemo-speech:parakeet-tdt-0.6b-v3"`, `en = "onnx:parakeet-tdt-0.6b-v2"`, `es = ...`.
- **Normalização de tempo em um ponto só** (`pipeline/run`), como no CrunchLog.

---

## 5. Escolhas de componente (justificativa em PESQUISA §8)

| Componente | Escolha | Por quê (resumo) | Fallback |
|---|---|---|---|
| ASR offline pt-BR/ES | **Parakeet TDT 0.6B v3** (FLEURS pt 4,76 / es 3,45; CC-BY-4.0; timestamps) via NeMo-Speech.cpp (GPU/CPU) ou onnx-asr int8 (CPU) | melhor relação qualidade/tamanho com timestamps; pilha já validada | fine-tune TAGARELA (pt-BR) ou Nemotron 3.5 offline se o `bench` ganhar |
| ASR offline EN | Parakeet v2 (onnx-asr) **ou** Nemotron Speech Streaming en (NeMo-Speech.cpp: WER 2,5 % LibriSpeech, 120× no GB10) | o autor mediu v2 > v3 em inglês; decidir no `bench` | — |
| ASR ao vivo | **Nemotron 3.5 ASR Streaming 0.6B** (pt-BR explícito, 80 ms–1,12 s) via WebSocket do `nemo-speech serve` | único streaming cache-aware aberto com pt-BR; OpenMDW | export ONNX no sherpa-onnx (≥ 1.13.5) |
| Diarização GPU / live | **Nemotron 3 Diarization** (8 falantes, streaming+offline, OpenMDW) no mesmo sidecar | melhor DER publicado; `transcribe --diarize --json` já casa palavra↔falante | Sortformer v2 |
| Diarização CPU sem torch | sherpa-onnx seg-3.0 + **WeSpeaker ResNet34-LM/CAM++** | Windows/mac sem GPU; wheels puros | TitaNet-small |
| Reidentificação | voiceprints locais opt-in (mesmo embedding ONNX), centróide por pessoa × canal, confirmação humana | §PESQUISA 3.3 | — |
| Captura Linux | MVP: 2× `pw-record` + alinhamento por correlação cruzada far×mic; v2: helper Rust `pipewire-rs` com dois streams no mesmo relógio | sem binding Python mantido para libpipewire | `parec` |
| Captura Windows | WASAPI loopback (PyAudioWPatch) in-process, mediana de timestamps de buffer, keep-alive de zeros | padrão validado | — |
| Captura macOS | helper Swift com Core Audio process tap (14.2+), agregado mic+tap em um relógio; avaliar AudioTee/SystemAudioKit como base **se a licença permitir** [incerto] | sem driver; permissão leve | ScreenCaptureKit |
| Eco | gate 3 dB padrão; **AEC3 opcional** (`pywebrtc-audio`, wheels aarch64) ligado só sem fone | gate barato e sem calibração; AEC só se o bench mostrar ganho | — |
| Resumo | **Ollama padrão** (`qwen3.6:35b-a3b`, thinking off, JSON Schema via `format`); `claude -p --json-schema` / `codex exec --output-schema` **opt-in** | privacidade; mesmo schema nos três | Gemma 4 26B-A4B, gpt-oss-120b |
| Busca | SQLite `ata.db`: FTS5 + sqlite-vec, RRF k=60, filtros (data, falante, projeto, idioma); embeddings Qwen3-Embedding-0.6B (MRL 512) ou bge-m3 (já instalado) | um arquivo, sem serviço; reconstruível | LanceDB se passar de ~100 k chunks |
| MCP | SDK `mcp` 2.x (`MCPServer`), stdio + Streamable HTTP loopback; handles explícitos (`job_id`), sem sampling (deprecado na spec 2026-07-28) | spec vigente | — |
| Dashboard | servidor HTTP stdlib + SSE no **v1** (V7; o MVP é CLI + MCP); FastAPI só se precisar de WebSocket | menos dependências | — |
| Demo / TTS | Piper (pt_BR: faber, cadu, edresson, jeff; en/es) | roda nos 3 SOs; gabarito por idioma | vozes do SO |
| Empacotamento | `uv tool install`, `ata engine install` (baixa NeMo-Speech.cpp e modelos com sha256), `ata doctor` | mesma ergonomia do CrunchLog | — |

---

## 6. Funcionalidades por prioridade

### MVP (Linux, CLI + MCP) — "grava, transcreve em pt-BR, nota no cérebro"

| # | Funcionalidade | Superior ao CrunchLog em |
|---|---|---|
| M1 | `ata start/stop/toggle/status` no Linux (PipeWire), `meta.json` com `start_epoch` medido e alinhamento far×mic por correlação | CrunchLog não grava no Linux; nosso script atual não mede o início |
| M2 | ASR **por idioma** (pt-BR/EN/ES/auto) com Parakeet v3/v2; GPU via sidecar no GB10, CPU int8 no resto | só inglês, só CPU |
| M3 | Gate + diarização far + turns (ideias do CrunchLog), diarização opcional do mic (`--mic-speakers N`) | sem diarização do mic |
| M4 | Nota Markdown por idioma (títulos, fillers, legenda, `## Transcrição`), frontmatter compatível com Obsidian e astra-2cerebro | inglês hard-coded |
| M5 | Resumo **estruturado** (`summary.json`: tldr, tópicos com timestamp, decisões, ações com dono/prazo/evidência, perguntas) via Ollama por padrão; CLI por assinatura opt-in | só texto livre, só CLI |
| M6 | `ata demo --lang pt|en|es` com Piper e gabarito (teste e2e sem reunião) | demo só inglês, só vozes do SO |
| M7 | MCP: `status`, `list`, `get_note`, `get_transcript` (paginado), `get_summary`, `search` (lexical no MVP), `start/stop`, `process`, `rename_speakers`, `export`, `doctor` | paridade |
| M8 | `ata import <mp3/mp4>`, `ata export srt|vtt|txt|json|md`, `ata rerender`, `ata speakers` | paridade |
| M9 | `ata doctor` (plataforma, PipeWire, sidecar, GPU, modelos, Ollama, CLIs, disco, benchmark 20 s) e `ata bench` | doctor sem GPU nem bench de WER |
| M10 | Leitor de bundles do CrunchLog (`bundle_version: 2`) para migração e teste diferencial | — |

### v1 — "ao vivo, quem fala, pergunte"

| # | Funcionalidade | Superior em |
|---|---|---|
| V1 | **Live local** (Nemotron 3.5 via WS do sidecar): turnos ao vivo em `live/turns.jsonl`, página Live por SSE, cola pré-reunião marcada ao vivo; resumo por turno via Ollama | live preso à AssemblyAI (paga, nuvem, só EN) |
| V2 | Diarização **Nemotron 3** na GPU (8 falantes) e reconciliação live↔offline | 4 falantes práticos, sem reconciliação |
| V3 | **Voiceprints**: `ata voices enroll "Ana" <bundle> "Speaker 2"`, auto-rotulagem com confiança, `ata voices forget` | só renomear à mão |
| V4 | **Busca híbrida + ask com citação**: `ata search`, `ata ask "o que decidimos sobre X?"` → `[2026-10-05 kickoff, 12:40]`; MCP `search`, `ask`, `actions`, `decisions`, `person`, `project`, `prep` semântico | scan lexical; prep por título |
| V5 | **Sync com segundo cérebro**: `ata connect cerebro --dir ~/…/astra` (escreve `fontes/AAAA-MM-DD-slug.md` + `wiki/fontes/`, decisões em `decisoes/registro.md`, wikilinks `[[pessoa]]`/`[[projeto]]`), `ata connect obsidian --vault …` | só cópia de arquivo para um vault |
| V6 | Plugin Claude Code + skills Codex (`meeting-notes`, `action-items`, `meeting-prep`, `ask-meetings`, `meeting-to-wiki`) | 3 skills |
| V7 | Dashboard local (gravar, ver faixas + transcrição sincronizada, renomear, editar texto de turno, settings, Live) | sem edição de transcrição |
| V8 | Windows: captura WASAPI, instalador, demo com Piper | paridade |

### v2 — "em todo lugar, sem fricção"

| # | Funcionalidade |
|---|---|
| X1 | macOS: helper Swift (tap + mic, um relógio) |
| X2 | Linux: helper Rust (`pipewire-rs`) com dois streams no mesmo relógio e captura por aplicação (Meet/Zoom) |
| X3 | AEC3 opcional; detecção de reunião (nós PipeWire ativos / sessões de áudio / processos) e auto start/stop; tray; notificações nos 3 SOs |
| X4 | Calendário (Google via conector já autorizado no openpcbotv3) para prep e título automático |
| X5 | **Agentic OS (opcional)**: `ata connect agentic-os` registrando o MCP em escopo usuário e um vault que a página Memory varre; Live page local alimentando a cola — mesmo desenho do CrunchLog, sem a AssemblyAI |
| X6 | Importação de gravações de celular (Apple Notes/Plaud), tradução pt↔en↔es da nota (Codex por assinatura), vídeo-aula a partir da reunião (skills INEMA) |

---

## 7. Estratégia multiplataforma

- **Linux é a plataforma primária** (host GB10, PipeWire 1.0.5). Preferência (decidida no spike da fase 0): **um processo, dois streams, um relógio** via PortAudio/`sounddevice` sobre `pipewire-pulse` (monitor do sink como input + mic, timestamps de callback) — o mesmo desenho do Windows. Se o spike falhar: `pw-record` × 2 (`--target <node.name>` + `stream.capture.sink=true`) com `start_epoch` **estimado** por primeiro buffer não nulo e corrigido por correlação cruzada far×mic quando há vazamento no mic; sem vazamento (fone), o meta declara `start_unmeasured`. v2: helper Rust (`pipewire-rs`) com `CLOCK_MONOTONIC` e captura por aplicação.
- **Windows**: in-process com PyAudioWPatch (loopback + mic no mesmo relógio QPC, mediana de timestamps, keep-alive de zeros, preenchimento pelo relógio quando o loopback cala).
- **macOS**: helper Swift (process tap 14.2+, agregado com mic, "System Audio Recording Only"); permissão negada = faixa `silent` + aviso, nunca erro mudo.
- **Mesmo contrato** em todos: `far.wav` + `mic.wav` 16 kHz mono PCM16, `meta.json` com `start_epoch` por faixa, eventos JSON por linha do helper.
- **Engines**: NeMo-Speech.cpp tem archives para `linux-{x86_64,aarch64}-{cpu,cuda12,cuda13,vulkan}`, `macos-aarch64-{metal,cpu}` e `windows-x86_64/aarch64` — um só motor nos 3 SOs; fallback Python puro (onnx-asr + sherpa-onnx) onde o binário falhar.
- **Instalação**: `uv tool install ata` + `ata engine install` (detecta GPU) + `ata setup --lang pt-BR`.

---

## 8. pt-BR / EN / ES de primeira classe

| Camada | O que muda por idioma |
|---|---|
| Bundle | `language` pedido (`pt-BR`, `en`, `es`, `auto`) e detectado (LID do Nemotron 3.5 ou do Whisper quando `auto`) |
| ASR | tabela `[asr]` por idioma; `auto` roda LID nos primeiros 30 s do far |
| Cleanup | fillers por idioma (`pt-BR`: "é", "né", "tipo", "hum", "ahn"… configuráveis; `es`: "este", "o sea", "eh"), repetições, glossário por idioma |
| Nota | templates de nota (`note.pt-BR.md`, `note.en.md`, `note.es.md`): título "Reunião", seções "Resumo / Decisões / Ações / Perguntas em aberto / Participantes / Problemas da gravação / Transcrição", formatos de data |
| Resumo | prompt-base em inglês com instrução de idioma **e** variante nativa; o `bench` decide; schema único |
| Prep | reconhece as seções pelo **schema** (`summary.json`), não pelos headings — funciona em qualquer idioma |
| Demo | roteiro **próprio**, escrito (não traduzido) nos 3 idiomas, com fatos verificáveis, 1 sobreposição e decisões/ações previstas; vozes Piper por idioma; gabarito por idioma |
| Live | `language` passado ao WebSocket; prompt do resumo por turno no idioma |
| Guia | `guia/index.html` PT + `guia/en/` + `guia/es/` (skill `projetos-landing-guia`) |

---

## 9. GPU no GB10 com fallback CPU

- `ata engine install` baixa o NeMo-Speech.cpp com backend detectado (`cuda13` no GB10; `metal` no Mac; `cpu` sem GPU) e os modelos (Parakeet v3, Nemotron 3.5 ASR, Nemotron 3 Diarization, Silero VAD, pontuação) com sha256.
- `ata engine start|stop|status` controla o `nemo-speech serve` em `127.0.0.1:<porta>` (sob `systemd-run --user --scope -p MemoryMax=` no Linux, regra da casa); o pipeline fala HTTP (`/v1/audio/transcriptions` com `word_timestamps` + `diarize`) e o live fala WS.
- `ata doctor` mede: RTF do ASR (20 s sintéticos + 60 s reais), RTF da diarização, latência do WS, memória; grava em `~/.cache/ata/doctor.json`; `[engine] prefer = "auto"` escolhe sidecar quando `rtf_gpu < rtf_cpu/2`.
- Fallback automático: sidecar ausente ou com erro → onnx-asr int8 + sherpa-onnx (mesmo resultado, mais lento), com aviso na nota (`engines` no meta).
- Memória unificada: nunca `--highvram`; faixa processada em janelas de 10 min (não carregar 2 h em float32).

---

## 10. Modelo de privacidade

| Nível | O que sai da máquina | Como se liga |
|---|---|---|
| **0 — cofre (padrão)** | nada: ASR, diarização, resumo (Ollama), embeddings, busca, live — tudo local | padrão após `ata setup` |
| **1 — assinatura** | texto da transcrição (nunca áudio) para Anthropic/OpenAI via `claude -p`/`codex exec`, com tools/MCP/hooks desligados e cwd vazio | `ata setup --summary claude|codex` com aviso; badge no dashboard; `ata doctor --privacy` lista |
| **2 — API** | nunca por padrão; só com autorização explícita do usuário para serviço e finalidade (regra do projeto) | não existe flag; exige mudança de config com nome do serviço |

Invariantes: logs, erros e `doctor` nunca ecoam texto de reunião; voiceprints são opt-in, locais, criptografados em repouso quando o SO oferecer keyring, apagáveis (`ata voices forget --all`); o índice `ata.db` é reconstruível e fica ao lado das gravações; `ata privacy report <bundle>` diz quais engines e destinos tocaram aquele bundle (lido do `meta.json.engines`).

---

## 11. Integração com agentes e segundo cérebro

**MCP (`ata mcp`, stdio; `ata mcp --http` em 127.0.0.1)** — tools com `outputSchema`/`structuredContent`, paginação `limit/cursor`, resultados com `meeting_id` + timestamp:

| Tool | Parâmetros | Nota |
|---|---|---|
| `ata_status` | — | fase, bundle atual, engine, live |
| `ata_list` | `from?, to?, participant?, project?, language?, limit?, cursor?` | |
| `ata_read` | `id, parts? (summary|decisions|actions|transcript), from_s?, to_s?` | |
| `ata_search` | `query, mode? (hybrid|lexical|semantic), filters…, limit?, rerank?` | trechos com `t_start`, `speaker`, `score`, `obsidian_url` |
| `ata_ask` | `question, filters…` | devolve evidências; a síntese fica com o agente (sem sampling) |
| `ata_actions` / `ata_decisions` | `status?, owner?, project?, since?` | entre reuniões |
| `ata_prep` | `title, participants?, days?` | cola semântica |
| `ata_person` / `ata_project` | `name` | linha do tempo |
| `ata_record_start/stop/toggle` | `title?` | lane do gravador |
| `ata_process` | `path, language?, speakers?` → `job_id` | handle explícito; `ata_job(job_id)` para progresso |
| `ata_rename_speakers`, `ata_export`, `ata_reindex`, `ata_doctor` | | |
| `ata_sync_cerebro` | `id, target (astra|obsidian|agentic-os), dry_run?` | escrita opt-in (`--write`) |

Resources `ata://<id>`, `ata://<id>/transcript`; prompts `/prep`, `/resumo-semana`, `/acoes-abertas`.

**Skills** (uma pasta por skill, servida a `.claude/skills` e `.agents/skills`): `meeting-notes`, `action-items`, `meeting-prep`, `ask-meetings`, `meeting-to-wiki`; plugin Claude Code (`.claude-plugin/plugin.json` + `marketplace.json` + `.mcp.json`); manifesto Codex (`agents/openai.yaml`).

**Segundo cérebro**: nota em `fontes/AAAA-MM-DD-slug.md` com frontmatter do kit (`tipo: fonte`, `subtipo: reuniao`, `atualizado`, `fontes`, `participants: ["[[ana]]"]`, `projeto: "[[x]]"`, `idioma`, `tags`), resumo em `wiki/fontes/`, decisões via `cerebro_registrar_decisao` (cerebro-mcp) ou escrita direta; ações abertas em `projetos/<nome>/acoes.md`. Obsidian: vault configurável, Bases de reuniões/ações, `obsidian://` para abrir. **Agentic OS (opcional, v2)**: `ata connect agentic-os` = registro do MCP em escopo usuário + vault varrido pela Memory + Live local alimentando a cola; `--undo`, `--status`, `--dry-run`.

---

## 12. Estratégia de testes e benchmark reproduzível

**Testes de código**
- Unitários + **property tests** (hypothesis): gate (far silente mantém tudo; faixas idênticas com margem 0 descartam só sobrepostas), turns/smoothing, normalização de tempo (`track_offsets` aplicados uma vez), leitura estrita de `meta.json`, fillers por idioma, schema do resumo.
- **Contratos**: round-trip JSON de `meta/words/turns/summary`; leitor de bundle CrunchLog v2.
- **Fakes, não mocks**: `FakeCapture` (gera WAVs sintéticos com offset conhecido), `FakeAsr`/`FakeDiarizer`/`FakeSummarizer` por protocolo, `fake-nemo-speech` (servidor HTTP/WS que devolve fixtures), `fake-claude`/`fake-codex` com modos de falha.
- **E2E sem reunião**: `ata demo --lang pt` → gabarito (`demo-reference.json`: fatos, segmentos de falante esperados, decisões/ações esperadas) → asserts.
- **Diferencial**: o mesmo bundle (demo do CrunchLog, `bundle_version: 2`) processado por `ata` e pelo `crunchlog` instalado → concordância de atribuição ME/FAR ≥ 0,95 e WER relativo entre as duas transcrições ≤ 5 % (CrunchLog só como oráculo local, uso permitido).
- Marcadores `slow`/`live`/`gpu`; a suíte padrão roda sem rede, sem modelos e em < 2 min.

**Benchmark (`ata bench`, resultados em `bench/results/<data>-<host>.json` + tabela Markdown)**

| Alvo | Conjunto | Métrica | Ferramenta |
|---|---|---|---|
| ASR pt-BR espontâneo | CORAA v1.1 subset 30 min (NURC/C-ORAL/SP-2010; baixado pelo harness, nunca redistribuído — conferir licença) + 1 gravação real do Nei (10 min, transcrição revisada) | WER, RTF, memória | `jiwer` com normalizador pt (pontuação, caixa, números) |
| ASR pt-BR leitura | FLEURS pt test (200 frases) | WER | idem |
| ASR EN | LibriSpeech test-clean subset (100 utt.) + AMI (1 reunião IHM) | WER | normalizador Whisper EN |
| ASR ES | FLEURS es test subset | WER | idem |
| Diarização | AMI (RTTM oficial), demo sintética pt/en/es (RTTM exato por construção), 3 × 10 min reais anotados no dashboard | DER (collar 0 e 0,25), contagem de falantes | `pyannote.metrics` (sem torch) |
| Duas faixas | demo pt-BR/EN/ES (Piper): mic = Eu + 2 vozes no far, bleed −18 dB, 1 sobreposição | ME/FAR corretos, palavras do gabarito ≥ 90 %, DER far ≤ 10 %, turnos ≥ 30 | harness próprio |
| Live | demo tocada em tempo real no WS | latência fim-de-turno p50/p95, WER live vs offline | harness próprio |
| Resumo | 10 reuniões (5 pt, 3 en, 2 es) | cobertura QA, fidelidade (FineSurE), G-Eval; juiz = `claude -p` (assinatura), candidatos = Ollama | `bench/summary` |
| Busca | 50 perguntas reais pt/en/es com resposta anotada | recall@5, MRR | `bench/search` |
| Reidentificação | 5 pessoas × 3 reuniões | precisão/recall da auto-rotulagem a 3 limiares | `bench/voices` |

Motores comparados na fase 0: Parakeet v2, v3, TAGARELA, Nemotron 3.5 (offline e @1,12 s), Qwen3-ASR 1.7B, Granite 4.1 2B, Whisper large-v3-turbo; diarizadores: Nemotron 3, sherpa (TitaNet vs WeSpeaker), pyannote community-1 (em venv separado).

---

## 13. Riscos e mitigações

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| NeMo-Speech.cpp `cuda13` aarch64 não roda no GB10 ou sem timestamps por palavra no Nemotron 3.5 | média | alto | fase 0 testa; fallback onnx-asr/sherpa (CPU) e NeMo em conda só para bench; Parakeet v3 via `nemo-speech` também |
| Parakeet v3 em pt-BR espontâneo pior que o esperado (treino pt-EU) | média | médio | bench com CORAA; TAGARELA/Nemotron 3.5 como alternativas; config por idioma |
| Qualidade de resumo pt-BR do Ollama abaixo do `claude -p` | alta | médio | nível 1 opt-in; bench de resumo; Gemma 4 / gpt-oss-120b; prompt nativo |
| Alinhamento far×mic com 2× `pw-record` impreciso | média | médio | medir por correlação; `damage_report`; helper Rust na v2 |
| Esforço do helper macOS | alta | médio | v2; avaliar AudioTee/SystemAudioKit (licença a conferir); Windows antes |
| Disciplina clean-room falha (cópia acidental) | baixa | alto | implementadores sem acesso a `_fonte/`; revisão de PR com checagem de similaridade; README com crédito às ideias |
| Disco a 95 % (211 GB) | alta | médio | orçamento por fase; modelos em `/mnt/hd8t` com symlink; limpeza antes da fase 0 |
| Voiceprints = biometria (LGPD) | média | alto | opt-in, local, apagável, sem export; aviso no setup |
| Modelos/licenças mudam (NC, gated) | média | médio | `models.lock` com licença por modelo; `ata doctor --licenses` |
| Um só mantenedor / build por agente | alta | médio | fases curtas com "pronto" mensurável; `longrun/` do método INEMA; testes como portão |

---

## 14. Plano de construção (recomendado: recriar do zero)

Tempo em semanas de build por agente, com revisão humana no fim de cada fase. Cada fase termina com commit + push (`inematds/ata`, autor `inematds`) e "pronto" verificável.

### Fase 0 — Fundação e medição (semana 1)

Entregas: repo público `inematds/ata` (MIT), `docs/CONTRATO.md` (bundle `ata/1`, protocolos, vocabulário), `ata doctor`, `ata engine install|start|status`, leitor de bundle CrunchLog v2, `ata bench asr|diar`, `longrun/` com goal/plan/state.

| Pronto quando | Comando → saída esperada |
|---|---|
| Engine instalado com GPU | `ata engine install && ata engine status` → `backend=cuda13 models=[parakeet-tdt-0.6b-v3, nemotron-3.5-asr-streaming-0.6b, nemotron-3-diarization, silero-vad] ok` |
| Doctor verde no GB10 | `ata doctor; echo $?` → todas as linhas `[ok]`/`[warn]`, `0` |
| Bench reproduzível | `ata bench asr --lang pt --set coraa-30min --engines parakeet-v3,nemotron-3.5,tagarela` → tabela `engine | WER | RTF | mem` e `bench/results/2026-10-xx-spark-922b.json` |
| Leitor de compatibilidade | `ata process ~/CrunchLog/recordings/2026-10-05-1642-demo --dry-run` → `bundle crunchlog/2 lido: 2 tracks, offsets=…` |
| **Spike: captura Linux em um só processo** (decide se o helper Rust é necessário) | `python -c "import sounddevice as sd; print([d for d in sd.query_devices() if 'Monitor' in d['name']])"` via `pipewire-pulse` → o monitor do sink aparece como **input**; teste de 60 s com dois streams no mesmo `sd.InputStream` host (mic + monitor), timestamps de callback (`time.inputBufferAdcTime`) e sem xrun → se passar, o MVP usa **um processo e um relógio** (mesmo desenho do Windows, zero Rust) e o helper Rust vira opcional; se falhar, fica o par de `pw-record` com a regra de `start_unmeasured` |

### Fase 1 — MVP Linux (semanas 2-4)

Entregas: captura PipeWire (M1), pipeline (M2-M3), nota por idioma (M4), resumo estruturado Ollama + CLI opt-in (M5), demo Piper (M6), MCP (M7), import/export/rerender/speakers (M8), doctor/bench (M9), testes (unit + property + e2e + diferencial), guia mínimo.

| Pronto quando | Comando → saída esperada |
|---|---|
| Demo pt-BR passa | `ata demo --lang pt-BR` → `nota: …/note.md; turnos ≥ gabarito.turns_min (ME ≥ gabarito.me_min); falantes far = gabarito.far_speakers; fatos ≥ 90 % do gabarito; ações ≥ 80 % do gabarito` e exit 0 (gabarito = `demo/pt-BR/reference.json` do **nosso** roteiro) |
| Demo EN e ES passam | `ata demo --lang en` e `--lang es` → idem, fatos ≥ 90 % |
| Gravação real alinhada | `ata start --slug teste && … && ata stop` → `meta.json` com `tracks[*].start_epoch` medidos pelo backend; `ata bench align <bundle>` → lag residual far×mic após correção `≤ 50 ms` **quando há vazamento do alto-falante no mic** (sem fone); com fone (sem vazamento) o meta registra `damage_reasons: ["start_unmeasured"]` em vez de fingir precisão; `damaged=false` no caso medido |
| Resumo estruturado | `ata summary <bundle> --provider ollama` → `summary.json` válido contra `schema/summary.json`, com `language: pt-BR` |
| MCP funciona no Claude Code | `claude mcp add ata -- ata mcp` e, numa sessão, `list recent meetings` → lista com `id`, `title`, `date` |
| Diferencial vs oráculo | `ata bench diff --crunchlog ~/CrunchLog/recordings/2026-10-05-1642-demo` → `me_far_agreement ≥ 0.95, rel_wer ≤ 0.05` |
| Suíte | `uv run pytest -q` → `passed`, 0 failed, < 2 min, sem rede |
| Privacidade | `ata doctor --privacy` → `nível 0: nada sai da máquina` |

### Fase 2 — Ao vivo, quem fala, pergunte (semanas 5-8)

Entregas: live local (V1), Nemotron 3 Diar + reconciliação (V2), voiceprints (V3), busca híbrida + ask + MCP v2 (V4), sync cérebro (V5), plugin/skills (V6).

| Pronto quando | Comando → saída esperada |
|---|---|
| Live local | `ata start --live` + demo tocada → `live/turns.jsonl` crescendo; `ata bench live` → `latência fim-de-turno p50 ≤ 1,5 s, p95 ≤ 3 s @chunk 1,12 s`; nota final vem do arquivo |
| Diarização GPU | `ata bench diar --engines nemotron-3,sherpa-wespeaker --set ami-1,demo-pt` → DER por engine; demo-pt `DER ≤ 10 %` |
| Reidentificação | `ata voices enroll "Ana" <b1> "Speaker 2"; ata process <b2>` → nota de b2 com `Ana` e `confiança=0.xx`; `ata bench voices` → precisão ≥ 0,9 no conjunto de 5×3 |
| Busca e ask | `ata ask "o que ficou decidido sobre o preço?"` → ≥ 1 evidência `[<reunião>, mm:ss]`; `ata bench search` → `recall@5 ≥ 0.8` |
| Cérebro | `ata connect cerebro --dir ~/projetos/astra-2cerebro/… --dry-run` → lista de arquivos a escrever; sem `--dry-run` → `fontes/2026-…-slug.md` e `wiki/fontes/…` criados, `decisoes/registro.md` com 1 linha nova |
| Skills | `claude plugin validate --strict ./plugins/ata` → ok; `/ata:meeting-notes latest` produz brief com `[mm:ss]` |

### Fase 3 — Dashboard, Windows, publicação (semanas 9-12)

Entregas: dashboard (V7), Windows (V8), guia trilíngue `guia/`, card no portal, curso derivado (opcional).

| Pronto quando | Comando → saída esperada |
|---|---|
| Dashboard | `ata dashboard` → `http://127.0.0.1:<porta>/?t=…`; gravar, ver faixas + transcrição sincronizada, renomear, editar turno, Live page |
| Windows | em máquina Windows: `uv tool install ata; ata setup; ata demo --lang pt-BR` → mesmos critérios da fase 1; `ata check-audio --tone` → far e mic com sinal |
| Guia e portal | `guia/index.html`, `guia/en/`, `guia/es/` publicados; `https://inematds.github.io/ata/guia/` responde; card no inema.club |

### Fase 4 — v2 (após a 12ª semana, por prioridade do usuário)

macOS helper (X1) · helper Rust Linux (X2) · AEC3, detecção de reunião, tray (X3) · calendário (X4) · **Agentic OS opcional** (X5) · importação mobile e tradução de notas (X6). Critérios no mesmo formato, definidos ao abrir cada item.

**Se o usuário preferir o fork**, o plano equivalente é: fase 0 = branch privada + `capture/linux.py` (dirigindo nosso recorder) + `language` no meta + motor por idioma em `pipeline/engines.py`; fase 1 = sidecar NeMo-Speech.cpp atrás de `AsrEngine`/`Diarizer` + live local substituindo o adaptador AssemblyAI; fase 2 = Ollama como `Summarizer` + notas pt-BR + busca; com "a suíte original (1 621 testes) continua verde" como critério em toda fase e um orçamento explícito de mudanças nos módulos SHARED. Não é o caminho recomendado pelos motivos do §0.

---

## 15. Decisões que o usuário precisa tomar

1. **Rumo**: recriar do zero (recomendado, repo público) ou fork privado.
2. **Nome**: Ata, Pauta ou Escriba (e o repo `inematds/<nome>`).
3. **Política de motor**: NeMo-Speech.cpp como motor primário (GPU/CPU, 3 SOs) com onnx-asr/sherpa de fallback — ou o inverso (Python puro primário, sidecar opcional).
4. **Resumo padrão**: Ollama (nível 0, `qwen3.6:35b-a3b`) por padrão e CLI por assinatura opt-in — ou CLI por padrão como no CrunchLog.
5. **Voiceprints**: desligados por padrão (opt-in no setup) — ou ligados com aviso.
6. **Escopo macOS**: helper próprio na v2, ou base em AudioTee/SystemAudioKit (depende da licença deles).
