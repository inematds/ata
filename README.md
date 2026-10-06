# Ata — notas de reunião que não saem da sua máquina

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

## 📖 Guia de uso

Guia completo (landing + passo a passo): **https://inematds.github.io/ata/guia/**

---

O **Ata** grava a sua reunião em **duas faixas**: o que você ouve (Meet, Zoom, Teams, YouTube) e o que você fala. Depois transcreve na sua máquina, em **português, inglês ou espanhol**, e entrega uma nota em Markdown com cada fala marcada por pessoa. Também gera resumo, decisões e ações com responsável. Não entra bot na call, não precisa de conta e o áudio nunca sai do computador.

- **Licença:** MIT. Projeto aberto do INEMA.
- **Inspiração:** as ideias do [CrunchLog](https://www.skool.com/aiautomationsbyjack), de Christian Landsteiner (duas faixas, "o microfone é você", gravação como fonte de verdade). O Ata é uma **implementação independente**: nenhuma linha de código do CrunchLog foi usada.
- **Estado:** 0.1.0. Todas as fases estão implementadas e testadas com motores simulados. Os motores reais (NeMo-Speech.cpp na GPU, onnx na CPU, Ollama) estão implementados, mas **ainda não foram medidos nesta versão** (veja [Estado e limites](#estado-e-limites)).

## O que ele faz

| Recurso | Como |
|---|---|
| **Duas faixas, sem driver** | Linux: PipeWire (`pw-record`). Windows: WASAPI loopback. macOS: helper Swift com *process tap* (14.2+, código-fonte em `helpers/macos/`) |
| **Microfone = você** | Tudo o que vem do microfone é "Eu". O *gate* tira do microfone o eco da caixa de som, e a separação de falantes só atua do outro lado |
| **pt-BR, EN, ES** | Motor, limpeza de vícios ("né", "tipo"; "um", "uh"; "este", "o sea"), nota, resumo, demo e preparação, tudo por idioma |
| **GPU ou CPU** | Motor principal: NeMo-Speech.cpp (Parakeet v3 + Nemotron, CUDA no DGX Spark/GB10, Metal no Mac, CPU nos demais). Reserva em processo: onnx-asr + sherpa-onnx |
| **Resumo local** | Ollama por padrão (JSON com resumo, tópicos, decisões, ações com dono e prazo, perguntas). Opcional: `claude` ou `codex` pela sua assinatura |
| **Busca e perguntas** | Índice SQLite (FTS5 + vetores) em todas as reuniões. `ata ask` responde com citação `[reunião, mm:ss]` |
| **Preparação** | `ata prep "cliente"` monta a pauta com ações em aberto, decisões e perguntas; os itens se marcam sozinhos na reunião seguinte |
| **Reconhecer vozes** | Opcional e desligado por padrão: cadastra a voz de uma pessoa e o "Pessoa 2" vira "Ana" nas próximas reuniões |
| **Segundo cérebro** | `ata connect cerebro --dir ...` escreve a reunião em `fontes/` e as decisões em `decisoes/registro.md`. Também há Obsidian e Agentic OS |
| **Modo ao vivo** | Transcrição local durante a call (Nemotron 3.5 streaming), com a pauta se marcando, sem serviço pago |
| **Agentes** | Servidor MCP (`ata mcp`), skills e plugin do Claude Code |
| **Dashboard** | `ata dashboard`: gravar, ouvir, transcrição clicável, renomear falantes, busca e modo ao vivo (PT/EN/ES) |
| **Compatível com o CrunchLog** | Lê gravações do CrunchLog (`bundle_version: 2`) sem alterar os originais |

## Instalar

```bash
# 1. uv (gerenciador de Python)
curl -LsSf https://astral.sh/uv/install.sh | sh                      # Linux/macOS
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows

# 2. Ata (com MCP, modo ao vivo e motor CPU)
git clone https://github.com/inematds/ata && cd ata
uv tool install --python 3.12 ".[mcp,live,onnx]"

# 3. Configurar (idioma, pastas, resumo)
ata setup --lang pt-BR --summary ollama

# 4. Motor de transcrição (detecta a GPU: cuda13 no GB10, metal no Mac, cpu no resto)
ata engine install --dry-run            # mostra o que vai baixar
ata engine install --allow-unverified   # nesta versão os hashes ainda não estão fixados
ata engine start

# 5. Conferir
ata doctor
```

## Usar

```bash
ata demo --lang pt-BR                  # reunião inventada → nota (com Piper; --no-tts sem voz)
ata toggle                             # começa a gravar; de novo para e gera a nota
ata start --title "Kickoff Acme" --speakers 3 --lang pt-BR
ata stop
ata import gravacao.mp4 --lang es      # áudio/vídeo que você já tem
ata process <gravação> --speakers 4    # reprocessar
ata speakers <gravação> "Pessoa 2=Ana" "Pessoa 3=Bruno"
ata export <gravação> --format srt     # srt|vtt|txt|json|md|csv
ata search "prazo do beta" --since 30d
ata ask "o que decidimos sobre o lançamento?"
ata actions --owner Ana
ata prep "acme" --calendar "Qui 14h, Ana e Bruno"
ata voices enroll Ana <gravação> "Pessoa 2"   # opcional (precisa de voices.enabled = true)
ata connect cerebro --dir ~/meu-cerebro
ata live start                         # modo ao vivo durante a gravação
ata dashboard                          # http://127.0.0.1:47530/
ata mcp                                # servidor MCP para Claude Code / Codex
ata privacy <gravação>                 # quais motores e destinos tocaram essa reunião
ata bench asr --set <pasta> --engines nemo,onnx --lang pt-BR   # WER e velocidade
```

Atalho de teclado: ligue `ata toggle` a uma tecla (GNOME/KDE, PowerToys/AutoHotkey, Atalhos do macOS ou Stream Deck).

**MCP no Claude Code:** `claude mcp add --scope user ata -- ata mcp`. O plugin com as skills (`meeting-notes`, `action-items`, `meeting-prep`, `ask-meetings`, `meeting-to-wiki`) está em `.claude-plugin/` e `skills/`.

## Como funciona

```
tecla / dashboard / MCP
   │
   ▼  gravação (PipeWire · WASAPI · helper macOS) → pasta da reunião (bundle ata/1)
far.wav (o que você ouve) + mic.wav (você) + meta.json (relógio, idioma, motores)
   │
   ▼  ata process (tudo local)
ASR por faixa e idioma → um único ponto de alinhamento de tempo → gate tira o eco do mic
→ separação de falantes do far (+ mic opcional) → voiceprints (opcional) → turnos
→ limpeza por idioma + glossário → resumo JSON (Ollama | claude | codex)
→ nota.md (bundle + pasta de notas) → índice de busca → segundo cérebro / Obsidian
```

Contrato completo: [`docs/CONTRATO.md`](docs/CONTRATO.md). Interfaces entre módulos: [`docs/INTERFACES.md`](docs/INTERFACES.md).

## Privacidade

| Nível | O que sai da máquina |
|---|---|
| **0 (padrão)** | Nada. Transcrição, separação de falantes, resumo (Ollama), busca e modo ao vivo rodam todos local |
| **1 (assinatura)** | Só texto da transcrição, nunca áudio, para Anthropic ou OpenAI via `claude -p` ou `codex exec`, com as ferramentas desligadas. Exige `privacy.level = 1` |

Nenhum módulo chama API paga. Logs e erros nunca contêm texto de reunião. As voiceprints ficam só na sua máquina e podem ser apagadas (`ata voices forget --all`).

## Estado e limites

- **Testes:** suíte com mais de 400 testes, sem rede e sem modelos (motores simulados). A gravação real pelo PipeWire foi testada no Linux.
- **Ainda não medido nesta versão:** desempenho e qualidade dos motores reais (NeMo-Speech.cpp, onnx, Ollama). A API do NeMo-Speech.cpp segue o que está documentado em `src/ata/engines/nemo.py`. Os hashes de verificação dos downloads estão vazios em `src/ata/data/models.toml`, por isso a instalação exige `--allow-unverified`.
- **macOS:** o helper Swift existe em código-fonte; ainda não foi compilado nem testado.
- **Windows:** captura implementada; não testada em máquina real nesta versão.

## Desenvolvimento

```bash
uv sync --all-groups
uv run pytest -q                       # suíte padrão (sem rede, sem modelos)
uv run pytest -q -m linux_audio        # gravação real via PipeWire
```

## Créditos

Inspirado nas ideias do CrunchLog (Christian Landsteiner). Modelos: NVIDIA Parakeet / Nemotron (NeMo-Speech.cpp), pyannote e WeSpeaker via sherpa-onnx, Ollama. Pesquisa e proposta de arquitetura: `docs/` e o projeto `crunchlog` do INEMA.
