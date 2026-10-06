# Pesquisa: estado da arte para um notetaker local-first (05/10/2026)

Pesquisa feita em 05/10/2026 para evoluir o CrunchLog 2.0.0a1 num produto superior, com pt-BR/EN/ES de primeira classe, Linux (GB10) como host principal e Windows/macOS como alvos. Nenhuma API paga foi chamada; só leitura de páginas públicas e de arquivos locais.

**Legenda de confiança**

- `[verificado]` — eu li a fonte primária (model card, release, doc oficial) nesta sessão.
- `[subagente]` — lido por um agente de pesquisa a partir da fonte indicada; não reconferi a página.
- `[incerto]` — não confirmado em fonte primária, ou número de hardware diferente do nosso; precisa de benchmark local.
- `[medido]` — medido neste host (spark-922b) ou pelo autor do CrunchLog nos docs dele.

**Host de referência (medido em 05/10/2026):** NVIDIA GB10, driver 580.95.05, CUDA 13.0, aarch64, Ubuntu 24.04.3, 119 GB RAM unificada, disco 211 GB livres (95% usado), PipeWire 1.0.5 + WirePlumber, Ollama 0.33.2 (com `qwen3.8:27b`, `qwen3.6:35b-a3b`, `qwen3:30b`, `bge-m3`, `llama3.1:70b` etc.). O env `crunchlog` (uv tool) tem onnxruntime 1.30.0 só CPU, onnx-asr 0.12.0, sherpa-onnx 1.13.8, modelos 673 MB. O env conda `chatterbox` do inemavox tem torch 2.10.0+cu130 com CUDA funcionando no GB10, NeMo 2.7.0, ctranslate2 4.7.1, faster-whisper 1.2.1 — prova de que NeMo/CUDA já roda nesta máquina. CrunchLog medido aqui: Parakeet v2 int8 CPU = 27,6x tempo real; demo de 158 s (duas faixas) processado em 57 s; resumo via `claude -p` em 10 s.

---

## 1. ASR offline local, multilíngue, com pt-BR forte

### 1.1 O que mudou desde o Parakeet v2

O CrunchLog usa `nemo-parakeet-tdt-0.6b-v2` (só inglês) via onnx-asr na CPU. Em 2026 há três famílias abertas que cobrem pt-BR com qualidade e timestamps por palavra: NVIDIA (Parakeet TDT v3 offline; **Nemotron 3.5 ASR Streaming 0.6B**, 04/06/2026), Qwen3-ASR (jan/2026) e Granite Speech 4.1 (29/04/2026). Whisper continua sendo a opção mais portátil, mas é maior, mais lento e sem WER pt publicado no card.

### 1.2 Tabela comparativa (WER em %, menor é melhor)

| Modelo | Params | pt? | WER pt | WER EN (Open ASR) | Timestamps palavra | Licença | Runtimes | Fonte |
|---|---|---|---|---|---|---|---|---|
| **Parakeet TDT 0.6B v3** | 0,6B | sim (25 idiomas europeus). **Treino em pt europeu; benchmarks em pt-BR** (Nota 2 do card) | FLEURS **4,76**, MLS 7,50, CoVoST 3,96; es 3,45; en 4,85 | 6,34 (card) | sim (palavra/segmento/char) | CC-BY-4.0 | NeMo; onnx-asr (`nemo-parakeet-tdt-0.6b-v3`); NeMo-Speech.cpp | [verificado] https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 |
| **Nemotron 3.5 ASR Streaming 0.6B** (04/06/2026) | 0,6B | **sim: pt-BR e pt-PT "transcription-ready"** (19 locales); es-ES/es-US; en-US/en-GB | FLEURS (LangID) **6,29** @80 ms → **5,48** @1,12 s; es 4,87→4,11; en 9,43→7,91 | não é a mesma régua do Open ASR | não confirmado no card [incerto] | **OpenMDW-1.1** | NeMo 26.06; **ONNX no sherpa-onnx ≥ 1.13.5** (11/08/2026, PRs #3732/#3734); NeMo-Speech.cpp | [verificado] https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b ; https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.5 |
| Canary 1B v2 | ~1B | sim (25 idiomas, pt europeu no treino) | só média FLEURS-25: 8,40 [incerto pt isolado] | 7,15 | sim (NeMo main) | CC-BY-4.0 | NeMo; onnx-asr; sherpa-onnx | [subagente] https://huggingface.co/nvidia/canary-1b-v2 |
| Qwen3-ASR 1.7B / 0.6B | 1,7B / 0,6B | sim (30 idiomas, LID) | só médias por conjunto [incerto] | 5,76 / 6,42 | via Qwen3-ForcedAligner-0.6B (pt: 38 ms de erro médio) | Apache-2.0 | transformers/vLLM; sherpa-onnx (≥ 1.12.34) | [subagente] https://huggingface.co/Qwen/Qwen3-ASR-1.7B |
| Granite Speech 4.1 2B (29/04/2026) | 2B | sim (en, fr, de, es, pt, ja) | Common Voice pt **7,86** | ~5,33 (busca; não confirmado) | variante `-plus` com timestamps e locutor | Apache-2.0 | transformers, vLLM, GGUF | [subagente] https://huggingface.co/ibm-granite/granite-speech-4.1-2b |
| Whisper large-v3 / large-v3-turbo | 1,55B / turbo | sim (~99 idiomas) | sem WER pt no card [incerto] | 7,44 (v3) | sim (faster-whisper, WhisperX) | MIT | faster-whisper (CTranslate2), whisper.cpp, WhisperX, MLX | [subagente] https://huggingface.co/openai/whisper-large-v3-turbo |
| Fine-tunes Whisper pt-BR (zuazo large-v3-pt 4,60 CV13; jlondonobo 4,84 CV11; fsicoli 7,56 CV19+FLEURS) | 1,55B | sim | 4,6–7,6 (datasets diferentes, não comparáveis) | — | sim | Apache-2.0 / MIT | faster-whisper | [subagente] links no §Fontes |
| Parakeet v3 **pt-BR fine-tune "TAGARELA"** (alefiury, ONNX/int8) | 0,6B | sim (pt-BR) | não lido (README inacessível) [incerto] | — | sim | [incerto] | onnx-asr | [subagente] https://huggingface.co/alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx |
| Cohere Transcribe 03-2026 | 2B | sim (14 idiomas) | só gráfico [incerto] | **5,42** (5º no leaderboard) | **não tem** | Apache-2.0 (gated) | transformers | [subagente] https://huggingface.co/CohereLabs/cohere-transcribe-03-2026 |
| Voxtral Mini 3B 2507 | ~4,7B | sim (8 idiomas) | só gráfico | 7,05 | não | Apache-2.0 | transformers, vLLM, GGUF | [subagente] https://huggingface.co/mistralai/Voxtral-Mini-3B-2507 |
| Moonshine | 27M–266M | **não** (en, es, de, ja, ko, zh, ar, vi, uk, tl) | — | 6,66 (streaming-medium) | — | MIT | moonshine-voice, sherpa-onnx | [subagente] https://github.com/moonshine-ai/moonshine |
| Kyutai STT | 1B / 2,6B | **não** (en+fr / en) | — | 6,40 | — | CC-BY-4.0 | PyTorch, Rust, MLX | [subagente] https://huggingface.co/kyutai/stt-1b-en_fr |
| NeMo `stt_pt_fastconformer_hybrid_large_pc` | 115M | pt-BR | CV16 12,03; MLS 24,78 | — | sim | **CC-BY-NC-4.0** | NeMo | [subagente] — descartar (fraco e NC) |

Notas:
- O autor do CrunchLog mediu, na gravação inglesa dele de 35 min, WER 0,100 no v2 contra 0,144 no v3 (ADR 0003) `[medido]`. Logo "trocar para v3" não é grátis em inglês: a escolha de motor deve ser **por idioma**.
- Open ASR Leaderboard (inglês, via API do dataset em 05/10/2026, `[subagente]`): 1º ARK-ASR-3B 4,76; 2º MOSS-Transcribe-preview-2B 4,87; 3º MOSS-Transcribe-Diarize 5,17; 4º moondream/parakeet-ultra 5,32; 5º Cohere Transcribe 5,42; 8º Qwen3-ASR-1.7B 5,76; 10º Parakeet v2 6,05; Canary 1B v2 7,15; whisper-large-v3 7,44. Os quatro primeiros são autodeclarados e não verifiquei idioma/licença. A trilha multilíngue (de/fr/it/es/pt) existe no paper v4 do leaderboard (arXiv 2510.06961) mas a tabela por modelo não foi extraída `[incerto]`.
- Datasets pt-BR para benchmark local: **CORAA ASR v1.1** (290,77 h, fala espontânea: ALIP, C-ORAL Brasil, NURC-Recife, SP-2010, TEDx) `[verificado]` https://github.com/nilc-nlp/CORAA — a cópia no HF (`nilc-nlp/CORAA-v1.1`) é gated (HTTP 401); FLEURS pt e Common Voice pt (leitura).

### 1.3 Recomendação (ASR offline)

| Papel | Escolha | Justificativa |
|---|---|---|
| Motor offline pt-BR/ES (padrão) | **Parakeet TDT 0.6B v3** via onnx-asr (mesma pilha do CrunchLog, troca de nome de modelo) | pt 4,76 / es 3,45 em FLEURS, timestamps nativos, CC-BY-4.0, CPU int8 já validado nesta pilha. Testar o fine-tune TAGARELA (pt-BR) no mesmo harness. |
| Motor offline EN | manter **Parakeet v2** (medição do autor: melhor que v3 em inglês) | zero mudança; decisão por `asr.language`. |
| Segunda opinião / modo qualidade | Nemotron 3.5 ASR @1,12 s em modo offline, Qwen3-ASR 1.7B, Granite 4.1 2B | só se o benchmark local em pt-BR espontâneo (CORAA + gravação real) mostrar ganho. |
| Portabilidade máxima (Mac/Win sem NVIDIA) | Parakeet v3 int8 via onnx-asr (CoreML/DirectML) ou whisper.cpp | `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")` é o exemplo oficial do README do onnx-asr ("Supports Parakeet v2 (En) / v3 (Multilingual)") `[verificado]` https://github.com/istupakov/onnx-asr ; providers CPU x86/Arm, CUDA, CoreML, DirectML `[subagente]`. |

---

## 2. Streaming / transcrição ao vivo local (substituir a AssemblyAI)

| Opção | Streaming real? | pt-BR | Latência | Qualidade | CPU/GPU | Fonte |
|---|---|---|---|---|---|---|
| **Nemotron 3.5 ASR Streaming 0.6B** | sim, cache-aware (FastConformer-CacheAware-RNNT) | **sim** | chunks de 80/160/320/560/1120 ms (`att_context_size`) | FLEURS pt 6,29→5,48 | GPU (H100 no card); ONNX (sherpa-onnx) e ggml (NeMo-Speech.cpp) para CPU | [verificado] card HF |
| **NeMo-Speech.cpp** (runtime oficial NVIDIA em ggml) | sim; `serve` expõe WebSocket `/v1/audio/transcriptions/realtime` com `word_timestamps`, `speaker_diarization`, `language`, `endpointing_ms`, `speech_contexts` | sim (Nemotron 3.5; Parakeet v3) | **Medido pela NVIDIA no DGX Spark (GB10, driver 580.126.09, CUDA 13.0, Nemotron Speech Streaming en 0.6B Q8_0): 6,6 ms/chunk @1,12 s = 120× tempo real; 4,7 ms/chunk @160 ms = 32×; WER 2,50-2,64 % (LibriSpeech test-clean)**. CPU i7-11700K: 27 ms/chunk (6×). RTX 4090: 2,3 ms (67×) | mesma do modelo | **CPU, CUDA, Metal, Vulkan**; instalador Linux/macOS/Windows; archives `linux-aarch64-cuda12/13`; o `install.sh` detecta `*dgx*spark*|*gb10*` → CUDA 13 quando driver ≥ 580 | [verificado] https://github.com/NVIDIA/NeMo-Speech.cpp (README, docs/install.md, docs/api.md, scripts/install.sh, **BENCHMARK.md seção "DGX Spark (GB10)"**) |
| Voxtral Mini 4B Realtime 2602 | sim, encoder causal | sim | 240 ms a 2,4 s | FLEURS pt 10,01 (160 ms) → 5,03 (480 ms) → 3,93 (2,4 s); offline 3,56 | GPU via vLLM | [subagente] https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602 |
| Qwen3-ASR em vLLM | sim (só vLLM, sem timestamps em streaming) | sim | [incerto] | próxima do offline | GPU | [subagente] |
| sherpa-onnx streaming zipformer | sim | só um modelo pt de terceiro sem README (`kroko`) [incerto] | baixa | [incerto] | CPU | [subagente] |
| WhisperLive / whisper_streaming (LocalAgreement) / whisper.cpp stream / faster-whisper + VAD | pseudo-streaming (re-decodifica janelas) | sim | 1–3 s+ | abaixo do offline em trechos curtos | CPU/GPU | [subagente] |
| Moonshine / Kyutai STT | sim | **não** | muito baixa | — | CPU / GPU | [subagente] |
| Vosk pt-BR | sim | sim (modelo antigo) | baixa | muito abaixo dos neurais atuais [incerto] | CPU | [subagente] |

**Recomendação (live local):** **Nemotron 3.5 ASR Streaming 0.6B** servido pelo **NeMo-Speech.cpp** (`nemo-speech serve`, WebSocket local), que já traz VAD (Silero), pontuação, ITN e diarização (Sortformer v2 / Nemotron 3) no mesmo binário, sem torch, com CPU 6x tempo real e CUDA no GB10. Alternativa de mesma qualidade em Python puro: o export ONNX do Nemotron 3.5 no sherpa-onnx (já dependência do CrunchLog). O contrato do CrunchLog (`docs/live.md`: segundo consumidor de PCM que nunca bloqueia a gravação; a gravação em arquivo continua sendo a fonte de verdade) fica intacto — só o adaptador AssemblyAI é substituído.

**Incertezas para medir:** latência fim a fim do Nemotron 3.5 na CPU do Mac/Windows; se o Nemotron 3.5 entrega timestamps por palavra via NeMo (o `serve` do NeMo-Speech.cpp entrega); build CUDA aarch64 do NeMo-Speech.cpp funcionando no GB10 (há archive prebuilt, mas não testei).

---

## 3. Diarização e reidentificação de falantes

### 3.1 Diarizadores

| Modelo | DER (referência) | Sobreposição | Streaming | Falantes | Runtime | Licença | Fonte |
|---|---|---|---|---|---|---|---|
| **Nemotron 3 Diarization** (NVIDIA, 23/09/2026) | CALLHOME-part2 full: **9,10** (30,4 s), 10,29 (1,04 s), 11,32 (0,32 s) vs Streaming Sortformer v2.1: 10,32 / 11,31 / 12,67; VoiceArena Diarization-Bench v1: 14,72 (1º de 12) [autodeclarado] | sim | **sim, mesmo checkpoint** (80 ms a 30,4 s) | até **8** | NeMo 3.0; **NeMo-Speech.cpp** (`nemo-speech diarize`, `transcribe --diarize --json` → `speaker` 1-based por palavra) | **OpenMDW-1.1**, "ready for commercial or non-commercial use" | [verificado] https://huggingface.co/nvidia/nemotron-3-diarization ; https://huggingface.co/blog/nvidia/nemotron-diarization |
| Streaming Sortformer 4spk-v2 | DIHARD III 18,91 (completo), 13,24 (1–4 spk); RTF 0,002 offline, 0,093 @1,04 s (RTX 6000 Ada) | sim | sim | 4 | NeMo; NeMo-Speech.cpp | CC-BY-4.0 | [subagente] https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2 |
| Sortformer 4spk-v1 | DIHARD3-eval 14,76 (1–4 spk) | sim | não | 4 | NeMo | **CC-BY-NC-4.0** | [subagente] |
| **pyannote speaker-diarization-community-1** (29/09/2025, pyannote.audio 4.x) | AMI IHM 17,0; AMI SDM 19,9; DIHARD3 20,2; VoxConverse 11,2; AISHELL-4 11,7 (3.1: 18,8 / 22,7 / 21,4 / 11,2 / 12,2) | sim (powerset) + saída "exclusive" | não | ilimitado (clustering) | PyTorch | CC-BY-4.0 (gated por cadastro) | [subagente] https://github.com/pyannote/pyannote-audio (README, tabela set/2025) |
| pyannote 3.1 | ver acima | sim | não | — | PyTorch | MIT | [subagente] |
| pyannote precision-2 | AMI IHM 12,9; DIHARD3 14,7 | sim | — | — | **nuvem paga** | — | fora de escopo |
| DiariZen-Large-s80-v2 (BUT) | AMI-SDM 13,9; DIHARD3 14,5; VoxConverse 9,1 | sim | não | — | PyTorch | código MIT, **pesos CC-BY-NC-4.0** | [subagente] https://github.com/BUTSpeechFIT/DiariZen |
| Senko | VoxConverse 13,9–17,7; AMI-IHM ~27 | **não** | não | contagem fraca | PyTorch/CoreML; 1 h em ~5 s (4090), 42 s CPU | não verificada | [subagente] https://github.com/narcotic-sh/senko |
| sherpa-onnx `OfflineSpeakerDiarization` (pyannote seg-3.0 ONNX + embedding) — **o que o CrunchLog usa** | não medido por nós [incerto] | segmentação sim | não | clustering | ONNX, sem torch, CPU | Apache-2.0 (runtime) | [subagente] releases `speaker-segmentation-models` / `speaker-recongition-models` |

### 3.2 Embeddings de falante (para reidentificação entre reuniões)

| Modelo | EER Vox1-O (%) | Params | ONNX no sherpa? | Licença | Fonte |
|---|---|---|---|---|---|
| ReDimNet2 B6 (12/03/2026) | **0,287** | 12,3M | não verificado | MIT (código) | [subagente] arXiv 2603.11841 |
| WeSpeaker ResNet293 LM+AS-Norm+QMF | 0,425 | 28,6M | sim (LM) | CC-BY-4.0 | [subagente] WeSpeaker README |
| 3D-Speaker ERes2NetV2 | 0,61 | 17,8M | sim (variante zh) | Apache-2.0 | [subagente] |
| NVIDIA TitaNet-Large | 0,66 | 23M | sim | CC-BY-4.0 | [subagente] |
| WeSpeaker CAM++ | 0,803 | 7,2M | sim | CC-BY-4.0 | [subagente] |
| SpeechBrain ECAPA-TDNN | 0,80 | ~15M | não verificado | Apache-2.0 | [subagente] |
| WeSpeaker ResNet34 (LM) | ~0,87 (sem LM) | 6,6M | sim | CC-BY-4.0 | [subagente] |
| NeMo TitaNet-small — **o que o CrunchLog usa** | não publicado na tabela | — | sim | CC-BY-4.0 | — |

Protocolos diferem (com/sem LM, AS-Norm, QMF); não comparar linha a linha. Todos são VoxCeleb (inglês, fala limpa): em áudio de chamada comprimido e em pt-BR a degradação é esperada e só um teste local calibra o limiar.

### 3.3 Esquema de cadastro de voz (voiceprint) — proposta de engenharia

Nenhuma fonte primária documenta isso em notetakers; OpenWhispr anuncia "impressão de voz" com nomes reais `[subagente]`. Esquema:

1. Depois da diarização, extrair um embedding por cluster usando só trechos **sem sobreposição** e com ≥ 5–10 s de fala líquida (calibrar).
2. Guardar por pessoa um **centróide** (média de embeddings L2-normalizados) **por canal** (mic vs far), com contagem de amostras, nome e versão do modelo (embeddings de modelos diferentes não são compatíveis).
3. Decidir por cosseno: atribui o nome só se `melhor ≥ limiar` **e** `melhor − segundo ≥ margem`; senão fica "Speaker N" e entra na fila de confirmação do usuário (`crunchlog speakers` já existe para isso).
4. Atualizar o centróide só com amostras confirmadas; limiar conservador (falso positivo = atribuir a pessoa errada, o erro mais caro).
5. Privacidade: voiceprint é dado biométrico (LGPD/GDPR). Local, opt-in, apagável, só o vetor.

### 3.4 Recomendação (diarização)

| Papel | Escolha | Justificativa |
|---|---|---|
| GPU no GB10 e live | **Nemotron 3 Diarization** via NeMo-Speech.cpp (ou NeMo) | melhor DER publicado, 8 falantes, streaming+offline no mesmo checkpoint, OpenMDW, runtime mantido pela NVIDIA nos 3 SOs |
| CPU sem torch (Win/Mac), padrão de compatibilidade | manter **sherpa-onnx seg-3.0** trocando o embedding TitaNet-small por **WeSpeaker ResNet34-LM** ou **CAM++** (ONNX já no sherpa) | mudança mínima; NeMo-Speech.cpp em CPU é a segunda via a medir |
| Alternativa Python bem testada | pyannote community-1 (4.x) | saída "exclusive" casa com ASR; exige torch (quebra o ADR 0003 no processo principal → só em sidecar) |
| Reidentificação | WeSpeaker ResNet34-LM / CAM++ (ONNX) hoje; ReDimNet2 quando houver ONNX | esquema §3.3 |

---

## 4. Captura de áudio (Linux / Windows / macOS) e eco

### 4.1 Tabela

| Plataforma | Técnica | Biblioteca | Driver? | Por app? | Maturidade | Fonte |
|---|---|---|---|---|---|---|
| Linux | monitor do sink via `pw-record --target <sink>` (+ `stream.capture.sink=true`) | PipeWire CLI | não | não | alta (é o que o `recorder_linux.py` faz) | [subagente] https://docs.pipewire.org/page_man_pw-cat_1.html |
| Linux | dois streams **no mesmo processo e mesmo relógio** (libpipewire) | **pipewire-rs** (oficial, `pipewire-sys` 0.10.1, 19/08/2026) ou cpal backend PipeWire | não | sim, por nó (`application.name`, `application.process.binary`) | média; exige Rust | [subagente] https://gitlab.freedesktop.org/pipewire/pipewire-rs |
| Linux | binding Python nativo | **não existe mantido** (`pipewire_python` só envolve `pw-cat`) | — | — | baixa | [subagente] |
| Linux | compat PulseAudio (`parec -d <sink>.monitor`, pulsectl, `soundcard`) | pipewire-pulse | não | não | alta [incerto, não testado] | [subagente] |
| Windows | WASAPI loopback | PyAudioWPatch (fork do PortAudio), `soundcard` | não | não | alta (CrunchLog usa) | [subagente] https://github.com/s0d3s/PyAudioWPatch |
| Windows | loopback **por processo** (`AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS`, `ActivateAudioInterfaceAsync`, Win10 2004+) | `windows-rs`/crate `wasapi` (Rust); sample oficial C++ | não | sim (incluir/excluir árvore de PID — serve para excluir o próprio app) | média; sem wrapper Python maduro | [subagente] https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params |
| macOS | Core Audio **process tap** (14.2+), permissão "System Audio Recording Only" | Swift (AudioTee, SystemAudioKit; o helper do CrunchLog) | não | sim (PIDs) | média/alta | [subagente] https://github.com/makeusabrew/audiotee |
| macOS | ScreenCaptureKit (13+; mic só 15+) | Swift | não | por app/janela | alta; permissão de Gravação de Tela (mais pesada) | [subagente] |
| 3 SOs | loopback único | **cpal** (Rust): Windows input em device de saída = loopback; macOS loopback exige 14.6+; Linux PipeWire 0.3.53+ | não | não | média; PR #894 (ScreenCapture) aberto | [subagente] https://github.com/RustAudio/cpal |
| 3 SOs | Electron `electron-audio-loopback` | Chromium | não | não | média | [subagente] |

Observações: no Windows o loopback para de entregar buffers quando nada toca (preencher com zeros pelo relógio, como o PLAN do CrunchLog já prevê) `[incerto]`; no macOS sem permissão o tap devolve **silêncio, não erro** (o CrunchLog marca a faixa como `silent` abaixo de −60 dBFS nos primeiros 20 s) `[medido pelo autor]`; portais Flatpak/Snap ainda não entregam áudio no ScreenCast `[subagente]`.

### 4.2 Eco: gate vs AEC

| Critério | Gate (CrunchLog) | AEC3 (WebRTC) |
|---|---|---|
| Custo | ~zero, reaproveita timestamps de palavra | CPU por amostra + dependência nativa |
| Depende de atraso | não | sim (erro de atraso degrada a cancelação) |
| Fala simultânea sem fone | descarta a palavra do mic quando far ≥ +3 dB e há palavra far sobreposta | preserva melhor em teoria |
| Efeito colateral | corta o usuário falando junto | pode distorcer a voz / induzir erro de ASR |

Opções de AEC: `pywebrtc-audio` (AEC3 + NS + AGC + VAD, wheels Linux x86_64/**aarch64**, macOS, Windows, `process(near, far)`) `[subagente]` https://github.com/Strands-Labs/pywebrtc-audio ; PipeWire `module-echo-cancel` (só Linux, `monitor.mode`, muda o grafo do usuário) `[subagente]` https://docs.pipewire.org/page_module_echo_cancel.html ; supressão de ruído (RNNoise, DeepFilterNet) **não** remove voz alheia e pode piorar o ASR.

**Recomendação (captura):** manter o padrão do ADR 0002 do CrunchLog — núcleo Python + **helper nativo por plataforma** com o mesmo contrato (`far.wav`, `mic.wav`, `meta.json`, um relógio). Linux: evoluir do par de `pw-record` para um `helpers/linux` em Rust (`pipewire-rs`) com dois streams no mesmo processo e `CLOCK_MONOTONIC` comum, dirigido por `capture/linux.py` como `capture/macos.py` dirige o helper Swift. Windows: manter PyAudioWPatch; Rust só se precisar de captura por processo. Eco: **gate continua padrão**; AEC3 offline (`pywebrtc-audio` sobre `mic.wav` × `far.wav`) como opção ligada só sem fone e só se o benchmark mostrar ganho. Avaliar um helper único em cpal nos 3 SOs antes de manter três helpers.

---

## 5. Resumos com LLM (local e por assinatura)

### 5.1 Modelos locais via Ollama (o que cabe em 128 GB e fala pt-BR)

| Modelo | Tamanho / licença | Contexto | Velocidade no DGX Spark/GB10 (decode, 1 stream) | Observação | Fonte |
|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B** / Qwen3.6-27B | 23–24 GB / 18–19 GB no Ollama [verificado]; Apache-2.0 [subagente] | **256K** [verificado] | "mais de 100 tok/s" (relato de usuário, vLLM) [incerto]; Qwen3-30B-A3B: 85 tok/s Ollama Q4 [subagente] | `qwen3.6:35b-a3b` já instalado aqui; thinking desligável | https://ollama.com/library/qwen3.6 ; fórum NVIDIA 10/08/2026 |
| Qwen3.8-27B (14/08/2026) | `qwen3.8:27b` já instalado aqui (17 GB) | [incerto] | — | mais novo; licença a conferir no card | [subagente] https://github.com/QwenLM/Qwen3.8 |
| **Gemma 4** 26B-A4B (MoE) / 31B denso / E2B / E4B (02/04/2026) | Apache-2.0; 140+ idiomas | 256K (26B/31B) | 26B-A4B: 49,6 Ollama / 54,9 vLLM (field notes) | JSON estruturado e system prompt nativos | [verificado] https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ ; [subagente] https://github.com/sergioamsilva/dgx-spark-field-notes |
| gpt-oss-120b / 20b (ago/2025) | Apache-2.0, MXFP4 (~65 GB / ~13 GB) | 131K | 120b: 42 tok/s Ollama, 61 vLLM; prefill ~1.956 tok/s (llama.cpp) | treino majoritariamente em inglês [incerto] | [subagente] https://openai.com/index/introducing-gpt-oss/ ; llama.cpp #16578 |
| Mistral Small 4 (16/03/2026) | 119B/~6B ativos, Apache-2.0 | 256K | — | ~60–70 GB em 4 bits [incerto] | [subagente] https://mistral.ai/news/mistral-small-4/ |
| Nemotron 3 Super 120B-A12B (11/03/2026) | ~65 GiB Q4_K; licença com conflito entre fontes (OpenMDW vs NVIDIA Open Model License) | — | suporte oficial no DGX Spark | ler a licença no card | [subagente] |
| Sabiá-4 (Maritaca) | **fechado, só API** | 128K | — | melhor em pt-BR segundo o próprio relatório; fora do critério local | [subagente] arXiv 2603.10213 |
| Tucano 2 3,7B (USP) | aberto, pt-BR | 4K | — | pequeno demais para 15k tokens | [subagente] arXiv 2603.03543 |

Lacuna: **não há benchmark público de resumo de reunião em pt-BR** para esses modelos; o Open PT-BR LLM Leaderboard (eduagarcia) não foi extraído. A escolha final exige teste próprio (5–10 reuniões reais, 3 candidatos, juiz G-Eval + QA por `claude -p`, revisão humana de amostra). Uma reunião de 1 h ≈ 10–15k tokens cabe inteira nos contextos de 128K–256K: map-reduce só acima de ~2–3 h.

### 5.2 Saída estruturada

| Mecanismo | Como | Limite | Fonte |
|---|---|---|---|
| Ollama `format` = JSON Schema | decodificação restrita; também `response_format` na API OpenAI-compatível | incluir o schema também no prompt; `temperature: 0` | [subagente] https://docs.ollama.com/capabilities/structured-outputs |
| llama.cpp | JSON Schema → GBNF (`json_schema` no `llama-server`) | GBNF manual é mais lento | [subagente] |
| vLLM | `guided_json` (xgrammar/guidance) | — | [subagente] |
| `claude -p --output-format json --json-schema '<schema>'` | resultado em `structured_output` | validação pós-geração; **não usar `--bare`** (ignora OAuth, exige API key) | [subagente] https://code.claude.com/docs/en/headless |
| `codex exec --output-schema schema.json -o out.json` | schema estrito (`additionalProperties: false`, todos em `required`) | prompt por argumento ou stdin (fechar stdin) | [subagente] https://developers.openai.com/codex/noninteractive |

### 5.3 Padrões de prompt e avaliação

- Esquema único nos três caminhos: `tldr`, `topics[]` (título, resumo, `t_start`), `decisions[]`, `action_items[]` (`text`, `owner`, `due`, `evidence` com timestamp), `open_questions[]`. `owner`/`due` = `null` quando não ditos (evita invenção).
- Com duas faixas, o dono "Eu" vem da estrutura (mic = Eu), não de inferência.
- Instrução em inglês + "write in Brazilian Portuguese" vs prompt em pt-BR: sem evidência publicada; medir com o juiz `[incerto]`.
- Avaliação: QA-based (cobertura), FineSurE (fidelidade por afirmação, arXiv 2407.00908), G-Eval (fluência/coerência/consistência/relevância). Juiz ≠ candidato.
- Privacidade: `claude -p` carrega CLAUDE.md/skills/MCP do diretório — rodar de um diretório limpo e com ferramentas desligadas (o CrunchLog já faz `tools disabled`).

### 5.4 Custo e privacidade

| | Ollama local | CLI por assinatura (`claude -p` / `codex exec`) | API |
|---|---|---|---|
| Custo marginal | energia | zero até o limite do plano | por token (vetado neste projeto sem autorização) |
| Dado sai da máquina | não | sim (transcrição) | sim |
| Saída estruturada | restrita na decodificação | validada depois | varia |
| Qualidade pt-BR | a medir | a mais alta disponível | idem |

**Recomendação:** Ollama como **padrão** (`qwen3.6:35b-a3b`, thinking off, temperatura baixa; alternativas em teste cego: Gemma 4 26B-A4B, gpt-oss-120b; modo qualidade: Qwen3.8-27B se licença/Ollama confirmarem), CLI por assinatura como **opt-in explícito** com aviso (como o CrunchLog já faz), API nunca por padrão.

---

## 6. Concorrentes e alternativas open source

| Produto | Licença / stack | Plataformas | ASR | Diarização | Resumo | pt-BR | Preço | Fonte |
|---|---|---|---|---|---|---|---|---|
| **Anarlog / Char / Hyprnote** (fastrepl) | MIT (comunidade) + enterprise; Tauri 2, React, Rust, SQLite | mac, win, Linux, mobile beta | local (Mac) ou nuvem/BYOK | crate própria (qualidade não verificada) | BYOK / Ollama / servidor OpenAI-compat | [incerto] | grátis | [subagente] https://github.com/fastrepl/anarlog |
| **Meetily** (Zackriya) | MIT; Rust + Tauri | mac, win; **Linux só build** | Whisper / Parakeet local | **só no PRO** ("planejada") | Ollama / OpenAI-compat | [incerto] | grátis / PRO | [subagente] https://github.com/Zackriya-Solutions/meetily |
| **OpenWhispr** | MIT | mac, win, Linux | local (Whisper, Parakeet, Cohere Transcribe) ou nuvem | ao vivo, **impressão de voz com nomes reais; "sua faixa carrega seu nome"** | sim | [incerto] | grátis (+ nuvem) | [subagente] https://github.com/OpenWhispr/openwhispr |
| **Scriberr** | open source; Go + Svelte, binário único | self-hosted | WhisperX / Parakeet / Canary, timestamps por palavra | pyannote local | Ollama / ChatGPT, prompts editáveis | [incerto] | grátis | [subagente] https://github.com/rishikanthc/Scriberr |
| WhisperX | BSD-4 | Python | Whisper + alinhamento forçado | pyannote | não | via Whisper | grátis | [subagente] |
| noScribe / Vibe / Buzz | Whisper offline; Vibe e Buzz MIT | desktop | Whisper | noScribe e Vibe sim; **Buzz não** | — | via Whisper | grátis | [subagente] |
| Amurex | extensão Chrome (Meet/Teams) | navegador | [incerto] | — | sugestões em tempo real, recap | [incerto] | — | [subagente] |
| Screenpipe | **source-available** (não OSI), MCP | desktop | local | — | via MCP | [incerto] | grátis a US$ 42 | [subagente] |
| **Granola** | fechado | mac, win | nuvem | — | nuvem; nota = seus bullets + transcrição | [incerto] | grátis; US$ 14 (Business, com API e **MCP** oficial: `query_granola_meetings`, `list_meetings`, `get_meetings`, `get_meeting_transcript`…) | [subagente] https://docs.granola.ai/help-center/sharing/integrations/mcp |
| Notion AI Meeting Notes | fechado | web, mobile | nuvem, 16 idiomas **incl. português** | só em inglês | nuvem | **sim** | US$ 20 | [subagente] |
| Otter.ai | fechado | web | nuvem (en, es, fr) | sim | nuvem | **não** | US$ 16,99 | [subagente] |
| Fireflies / tl;dv / Krisp | fechados; bot ou app | web | nuvem (100+ / 30+ / 16+ idiomas) | sim | nuvem | provável [incerto] | US$ 8–18 | [subagente] (fontes secundárias) |
| Apple Notes (iOS/macOS 26) | fechado | Apple | on-device, **português** | — | Apple Intelligence | sim | incluso | [subagente] |
| Limitless / Plaud | hardware; Limitless comprada pela Meta (05/12/2025), vendas cessadas | — | nuvem | — | nuvem | — | — | [subagente] |

**Funcionalidades que valem copiar:** notas em Markdown + SQLite legíveis (Anarlog); MCP e CLI próprios (Anarlog, Granola); rótulo do usuário na própria faixa (OpenWhispr — o CrunchLog já faz por construção); detecção de Zoom/Teams e vínculo com calendário (OpenWhispr, Meetily PRO); timestamps por palavra com alinhamento (WhisperX, Scriberr); pastas e pergunta conversacional sobre o histórico (Granola); prompts de resumo editáveis (Scriberr, Meetily — o CrunchLog tem templates); resumo com `evidence` (trecho + timestamp) — ninguém faz, vale fazer.

**O que o CrunchLog faz de único e deve ser preservado:** "Eu" sem inferência (mic = Eu), diarização só na faixa remota, itens de ação com dono confiável, privacidade verificável (o áudio do mic nunca sai), nota Markdown com todos os turnos, `rerender` sem retranscrever, `doctor` com benchmark, resumo por assinatura sem API key.

---

## 7. Integração com agentes, segundo cérebro e busca semântica

### 7.1 MCP em 2026 — o que mudou (revisão **2026-07-28**) `[verificado]`

Fonte: https://modelcontextprotocol.io/specification/2026-07-28/changelog

- **Stateless**: removidos `initialize`/`notifications/initialized` e `Mcp-Session-Id`; cada request leva `protocolVersion`/`clientCapabilities` em `_meta`; novo RPC obrigatório `server/discover` (SEP-2575, SEP-2567). Estado entre chamadas = **handles** explícitos passados como argumento de tool (ex.: `job_id`).
- **Sampling, Roots e Logging deprecados** (SEP-2577): "integrate directly with LLM provider APIs instead of Sampling". O servidor que precisa de LLM chama ele mesmo (Ollama/CLI).
- **MRTR** (SEP-2322): perguntas ao usuário via `resultType: "input_required"` + `inputRequests`; todo resultado tem `resultType`.
- **Tasks** viram extensão `io.modelcontextprotocol/tasks` com polling `tasks/get` (SEP-2663).
- Transportes: stdio e Streamable HTTP; HTTP+SSE deprecado.
- SDK Python oficial `mcp` **2.3.0** (PyPI), classe `MCPServer` `[verificado]` https://pypi.org/project/mcp/ . O CrunchLog já suporta "1.x com FastMCP, 2.x com MCPServer" (docs/features/mcp.md).

MCPs de reuniões existentes (padrão comum: listar por período, obter resumo, obter transcript verbatim, buscar; quase todos só leitura): Granola, Fireflies (`fireflies_get_transcripts`, `fireflies_get_summary`, `fireflies_search`), Otter (`search` + `fetch`), tl;dv, Zoom, Notion `[subagente]`.

### 7.2 Claude Code e Codex `[subagente]`

- Claude Code: skills em `SKILL.md` (comandos foram fundidos em skills); plugins com `.claude-plugin/plugin.json` (skills, agents, hooks, MCP); marketplace por repo git; `claude mcp add` (stdio/http, escopos local/projeto/usuário); hooks (`SessionEnd`, `PostToolUse`, `FileChanged`…); headless `claude -p --output-format json --json-schema`. https://code.claude.com/docs/en/skills , /plugins , /mcp , /hooks , /headless
- Codex: `AGENTS.md` (global `~/.codex/AGENTS.md`), skills em `.agents/skills` (`SKILL.md` + `agents/openai.yaml`), MCP em `~/.codex/config.toml` (`[mcp_servers.x]`), `codex exec --output-schema`. https://developers.openai.com/codex/skills , /codex/mcp , /codex/noninteractive
- O plugin do CrunchLog já segue isso: `plugins/crunchlog/` com `.claude-plugin/plugin.json`, `marketplace.json`, `.mcp.json`, `.codex-plugin/plugin.json` e 3 skills (`crunchlog`, `meeting-notes`, `action-items`).

### 7.3 Obsidian e segundo cérebro `[subagente]`

- Obsidian URI (`obsidian://open?vault=&file=`, `new`, `daily`, `search`); plugin **Local REST API** (`127.0.0.1:27124`, chave; agora com servidor MCP em `/mcp/`) https://github.com/coddingtonbear/obsidian-local-rest-api ; **Bases** (core, visões tipo banco sobre propriedades) e Dataview; propriedades especiais `tags`, `aliases`, `cssclasses`.
- "Claude OS / Agentic OS" de Jack Roberts: sem fonte pública primária (aula fechada no Skool). Pelo doc do CrunchLog: app local Bun + Vite que roda o `claude` CLI; página **Connections** lista os MCP de escopo usuário (`claude mcp list`); página **Memory** sincroniza `*.md` de vaults Obsidian em `~/Obsidian`, `~/Documents/Obsidian`, iCloud e `obsidian.json`.
- **astra-2cerebro** (local, v1.0.0, MIT): kit Markdown com `AGENTS.md`/`CLAUDE.md` idênticos (mapa de rotas), pastas `contexto/`, `conexoes.md`, `decisoes/registro.md`, `projetos/<nome>/README.md`, `fontes/` (bruto, imutável, `AAAA-MM-DD-slug.md`), `wiki/{index,log}.md` + `wiki/{entidades,conceitos,fontes}/` com frontmatter `tipo`, `atualizado`, `fontes: [...]`, wikilinks `[[slug]]`; skills `/iniciar /entrevista /wiki /vincular /auditar /evoluir /rotina /cerebro-3d`. A skill `wiki` cita explicitamente "pessoas, projetos, reuniões e decisões".
- **cerebro-mcp** (local, v1.0.0, Node ≥ 20, stdio ou Streamable HTTP em 127.0.0.1): 13 tools — leitura (`cerebro_contexto`, `cerebro_prioridades`, `cerebro_rotas`, `cerebro_buscar` lexical com pesos, `cerebro_ler`, `cerebro_wiki_indice`, `cerebro_wiki_pagina`, `cerebro_projetos`, `cerebro_conexoes`, `cerebro_rotinas`) e escrita opt-in `--escrita` (`cerebro_registrar_decisao`, `cerebro_adicionar_fonte` com `wx`, `cerebro_registrar_execucao`); recursos `cerebro://<caminho>`; busca lexical sem índice; HTTP sem auth.

### 7.4 Busca semântica local `[subagente]`

| Modelo de embedding | Params | Dim | Contexto | Licença | MTEB-BR (22 tarefas pt-BR) | Ollama |
|---|---|---|---|---|---|---|
| **Qwen3-Embedding-0.6B** | 0,6B | 1024 (MRL 32–1024) | 32k | Apache-2.0 | n/d | sim |
| Qwen3-Embedding-4B / 8B | 4B / 7,6B | até 2560 / 4096 [incerto] | 32k | Apache-2.0 | 0,662 (6º) / **0,670 (2º)** | sim |
| EmbeddingGemma-300M | 308M | 768 (128–768) | 2k | Gemma (termos) | 0,649 (13º) | sim |
| bge-m3 (**já instalado aqui**) | 568M | 1024 | 8192 | MIT | n/d | sim |
| nomic-embed-text-v2-moe | 475M | 768 | 512 | Apache-2.0 | n/d | sim |
| multilingual-e5-large-instruct | 560M | 1024 | 512 | MIT | n/d | [incerto] |
| snowflake-arctic-embed-l-v2.0 | 568M | 1024 | 8192 | Apache-2.0 | n/d | sim |
| jina-embeddings v3/v5 | ~600M | 1024 | 8k–32k | **CC-BY-NC** | — | não |

Fontes: https://mteb-br.org/ (arXiv 2607.04581, jul/2026); MTEB-PT (arXiv 2607.04071): ranking multilíngue não prevê bem o pt. Rerankers: bge-reranker-v2-m3 (Apache-2.0), Qwen3-Reranker-0.6B/4B (Apache-2.0); Ollama ainda sem API de rerank de primeira classe (issue #16076). Vector stores embutidos: **sqlite-vec** 0.1.9 (31/03/2026; mesmo arquivo do FTS5; busca exata) https://github.com/asg017/sqlite-vec ; LanceDB 0.39 (17/09/2026; ANN + BM25 + hybrid) https://github.com/lancedb/lancedb . Padrão: FTS5 BM25 + vetor, fusão **RRF (k=60)**, filtros por data/falante/projeto, reranker opcional nos 30–50 melhores.

**Recomendação (integração):** manter o MCP stdio do CrunchLog e acrescentar tools de busca híbrida com citação (`[reunião, mm:ss]`), ações/decisões entre reuniões, `prep` semântico, `job_id` para processamento (handle explícito, sem sessão), e escrita opt-in no segundo cérebro (via `cerebro_adicionar_fonte`/`cerebro_registrar_decisao` do cerebro-mcp ou diretamente em `fontes/` + `wiki/fontes/` com o frontmatter do kit). Índice: um `reunioes.db` SQLite (FTS5 + sqlite-vec) descartável; fonte canônica continua o Markdown. Embedding padrão `Qwen3-Embedding-0.6B` (MRL 512) via Ollama; `bge-m3` (já instalado) como alternativa imediata. Skills: `meeting-notes`, `action-items` (já existem), + `meeting-prep`, `ask-meetings`, `meeting-to-wiki`.

---

## 8. Recomendação consolidada por componente

| Componente | Hoje (CrunchLog 2.0.0a1) | Recomendado | Confiança |
|---|---|---|---|
| ASR offline | Parakeet v2 int8 (onnx-asr, CPU, só EN) | **por idioma**: v2 (EN), **Parakeet v3** (pt-BR/ES) na mesma pilha; TAGARELA e Nemotron 3.5 offline em benchmark | alta (fonte primária), WER local a medir |
| ASR GPU (GB10) | nenhum | **NeMo-Speech.cpp** (archive `linux-aarch64-cuda13`) como **sidecar** falando o protocolo `AsrEngine`; NeMo em conda só para benchmark | média (prebuilt existe; não testado aqui) |
| Live | AssemblyAI (nuvem, paga) | **Nemotron 3.5 ASR Streaming** via `nemo-speech serve` WebSocket local; alternativa sherpa-onnx ONNX | alta (fontes primárias); latência a medir |
| Diarização | sherpa-onnx seg-3.0 + TitaNet-small, só far | GPU/live: **Nemotron 3 Diarization** (NeMo-Speech.cpp); CPU: sherpa-onnx com embedding WeSpeaker; opcional diarizar o mic quando houver várias pessoas na sala | alta / média |
| Reidentificação | manual (`crunchlog speakers`) | voiceprints locais opt-in (WeSpeaker ResNet34-LM/CAM++ ONNX), centróide por pessoa e canal, confirmação humana | média (esquema próprio) |
| Captura Linux | nosso `recorder_linux.py` (2× `pw-record`) | `helpers/linux` (Rust, pipewire-rs, um relógio) + `capture/linux.py` | média |
| Captura Win/Mac | PyAudioWPatch / helper Swift | manter | alta |
| Eco | gate 3 dB | gate padrão; AEC3 opcional (pywebrtc-audio) | média |
| Resumo | `claude -p` / `codex exec`, EN | **Ollama padrão** (`qwen3.6:35b-a3b`), CLI opt-in, schema JSON único, pt-BR/EN/ES | média (qualidade a medir) |
| Busca | `search_notes` lexical | SQLite FTS5 + sqlite-vec + RRF, embeddings Qwen3-0.6B/bge-m3 | alta |
| MCP | stdio, 1.x/2.x | manter; novas tools; handles explícitos; sem sampling | alta |
| Segundo cérebro | vault Claude OS (cópia de nota) | lista de alvos de sync: Claude OS + astra-2cerebro (`fontes/` + wiki) | alta |

---

## 9. Incertezas que exigem benchmark local (ordem de prioridade)

1. WER pt-BR **em fala espontânea de reunião** (CORAA + gravação real) para Parakeet v3, TAGARELA, Nemotron 3.5 (offline), Qwen3-ASR 1.7B, Granite 4.1 2B, Whisper turbo. Idem EN (v2 vs v3) e ES.
2. NeMo-Speech.cpp no GB10: instalação do archive `cuda13`, RTF de ASR e diarização, latência do WebSocket, timestamps por palavra do Nemotron 3.5.
3. DER na nossa faixa remota (áudio de chamada comprimido, pt-BR): Nemotron 3 Diarization vs sherpa-onnx (TitaNet vs WeSpeaker) vs pyannote community-1.
4. Offset de início e deriva entre os dois `pw-record` em 1–2 h (correlação cruzada mic × far); viabilidade do helper Rust.
5. Gate vs AEC3 offline: WER e palavras do usuário preservadas, com e sem fone.
6. Qualidade do resumo pt-BR: Qwen3.6-35B-A3B vs Gemma 4 26B-A4B vs gpt-oss-120b vs `claude -p`, com juiz e amostra humana; prefill de 15k tokens no GB10.
7. Limiar de cosseno e fala mínima para voiceprint confiável em áudio de chamada.
8. Recall da busca híbrida com ~50 perguntas reais em pt/en/es; Qwen3-Embedding-0.6B vs bge-m3.
9. onnxruntime-gpu em aarch64 + CUDA 13 (para dar GPU ao onnx-asr/sherpa sem sidecar) `[incerto]`.
10. Disco: 211 GB livres (95%); cada ambiente/modelo novo precisa de orçamento.

---

## Fontes principais

**Verificadas nesta sessão (05/10/2026):** https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 · https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b · https://huggingface.co/nvidia/nemotron-3-diarization · https://huggingface.co/blog/nvidia/nemotron-diarization (23/09/2026) · https://github.com/NVIDIA/NeMo-Speech.cpp (README, docs/install.md, docs/cli.md, docs/api.md, scripts/install.sh) · https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.5 (11/08/2026) · https://github.com/istupakov/onnx-asr · https://github.com/nilc-nlp/CORAA · https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR (cadu, edresson, faber, jeff) · https://modelcontextprotocol.io/specification/2026-07-28/changelog · https://pypi.org/project/mcp/ (2.3.0) · https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ (02/04/2026) · https://ollama.com/library/qwen3.6 · docs e código do CrunchLog 2.0.0a1 (`docs/PLAN.md`, `docs/CONTEXT.md`, `docs/adr/*`, `docs/live.md`, `docs/integration/agentic-os.md`, `src/crunchlog/**`).

**Lidas pelos agentes de pesquisa:** https://huggingface.co/nvidia/canary-1b-v2 · https://huggingface.co/Qwen/Qwen3-ASR-1.7B · https://huggingface.co/ibm-granite/granite-speech-4.1-2b · https://huggingface.co/openai/whisper-large-v3-turbo · https://huggingface.co/CohereLabs/cohere-transcribe-03-2026 · https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602 · https://huggingface.co/mistralai/Voxtral-Mini-3B-2507 · https://github.com/moonshine-ai/moonshine · https://huggingface.co/kyutai/stt-1b-en_fr · https://huggingface.co/api/datasets/hf-audio/open-asr-leaderboard/leaderboard · https://arxiv.org/html/2510.06961v4 · https://huggingface.co/zuazo/whisper-large-v3-pt · https://huggingface.co/jlondonobo/whisper-large-v2-pt-v3 · https://huggingface.co/fsicoli/whisper-large-v3-pt-cv19-fleurs · https://huggingface.co/alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx · https://github.com/collabora/WhisperLive · https://github.com/ggml-org/whisper.cpp · https://github.com/pyannote/pyannote-audio · https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2 · https://huggingface.co/nvidia/diar_sortformer_4spk-v1 · https://github.com/BUTSpeechFIT/DiariZen · https://github.com/narcotic-sh/senko · https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models · https://k2-fsa.github.io/sherpa/onnx/speaker-identification/index.html · https://github.com/wenet-e2e/wespeaker · https://github.com/modelscope/3D-Speaker · https://github.com/IDRnD/redimnet · https://arxiv.org/abs/2603.11841 · https://huggingface.co/nvidia/speakerverification_en_titanet_large · https://docs.pipewire.org/page_man_pw-cat_1.html · https://docs.pipewire.org/page_module_echo_cancel.html · https://gitlab.freedesktop.org/pipewire/pipewire-rs · https://github.com/s0d3s/PyAudioWPatch · https://github.com/bastibe/SoundCard · https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording · https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params · https://www.recall.ai/blog/core-audio-taps · https://github.com/makeusabrew/audiotee · https://github.com/pieralukasz/SystemAudioKit · https://github.com/RustAudio/cpal · https://github.com/Strands-Labs/pywebrtc-audio · https://forums.developer.nvidia.com/t/measured-inference-benchmarks-on-a-single-dgx-spark-same-harness-across-ollama-llama-cpp-and-vllm-notes-data-published/379766 (10/08/2026) · https://github.com/sergioamsilva/dgx-spark-field-notes · https://github.com/ggml-org/llama.cpp/discussions/16578 · https://github.com/QwenLM/Qwen3.8 · https://openai.com/index/introducing-gpt-oss/ · https://mistral.ai/news/mistral-small-4/ · https://arxiv.org/html/2603.10213v1 · https://arxiv.org/html/2603.03543v1 · https://docs.ollama.com/capabilities/structured-outputs · https://code.claude.com/docs/en/headless · https://developers.openai.com/codex/noninteractive · https://docs.granola.ai/help-center/sharing/integrations/mcp · https://github.com/fastrepl/anarlog · https://github.com/Zackriya-Solutions/meetily · https://github.com/OpenWhispr/openwhispr · https://github.com/rishikanthc/Scriberr · https://github.com/m-bain/whisperX · https://support.apple.com/guide/iphone/record-and-transcribe-audio-iphbe11247b5/ios · https://code.claude.com/docs/en/skills · https://code.claude.com/docs/en/plugins · https://code.claude.com/docs/en/mcp · https://developers.openai.com/codex/skills · https://developers.openai.com/codex/mcp · https://github.com/coddingtonbear/obsidian-local-rest-api · https://mteb-br.org/ · https://arxiv.org/abs/2607.04581 · https://arxiv.org/abs/2607.04071 · https://github.com/asg017/sqlite-vec · https://github.com/lancedb/lancedb · https://github.com/ollama/ollama/issues/16076 · arquivos locais `~/projetos/astra-2cerebro/{README.md,template/CLAUDE.md,skills/wiki/SKILL.md}` e `~/projetos/cerebro-mcp/{README.md,docs/seguranca.md,docs/arquitetura.md,package.json}`.
