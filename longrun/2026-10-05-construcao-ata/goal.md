# Goal — construcao-ata

- **Início:** 2026-10-05 18:39 · **Agente:** Claude (sessão principal + subagentes por módulo)
- **Tetos:** testes sem rede e sem modelos (< 3 min); nenhum download de modelo; memória dos testes < 4 G

## Resultado
**Ata** — notetaker de reuniões local-first, implementação independente inspirada nas ideias do CrunchLog (sem código copiado), com todas as fases da proposta (`~/projetos/crunchlog/pesquisa/PROPOSTA-PRODUTO-SUPERIOR.md`) implementadas e testadas com motores simulados (o usuário dispensou testar nos modelos locais), publicado em `inematds/ata` com guia PT/EN/ES e no portal.

## Critérios de pronto (verificáveis)

**Função**
- [ ] `uv run pytest -q` → `≥ 200 passed`, `0 failed` (sem rede, sem modelos)
- [ ] `uv run ata --help` → lista `setup start stop toggle status process import export rerender speakers demo doctor bench engine search ask prep actions voices connect mcp dashboard live reindex privacy`
- [ ] `ATA_ENGINES=fake uv run ata demo --lang pt-BR --no-tts` → última linha `note: <caminho>`; a nota tem `## Transcrição`, linhas `Eu:` e `Pessoa 2:` e `## Decisões`
- [ ] idem `--lang en` → `## Transcript`, `Me:`; `--lang es` → `## Transcripción`, `Yo:`
- [ ] `uv run ata process --dry-run ~/CrunchLog/recordings/2026-10-05-1642-demo` → `bundle crunchlog/2 lido: 2 tracks`
- [ ] teste MCP por stdio → `tools/list` com ≥ 18 ferramentas `ata_*`
- [ ] dashboard em porta livre → `GET /` 200 e `GET /api/meetings` JSON
- [ ] `ata search` e `ata ask` sobre 3 reuniões de fixture devolvem trecho com `[reunião, mm:ss]`
- [ ] gravação Linux real: `ata start` + `pw-play` + `ata stop --no-process` → bundle com `far.wav` não-silencioso e `meta.json` `ata/1` válido

**Regressão**
- [ ] CrunchLog instalado intacto: `crunchlog --version` → `2.0.0a1`
- [ ] suíte inteira verde no commit final de cada fase

**Limite**
- [ ] `grep -rniE "assemblyai|api\.openai\.com|api\.anthropic\.com|openrouter|groq" src/` → vazio
- [ ] `grep -rln "crunchlog" src/ata` → só `src/ata/compat/crunchlog.py`
- [ ] nenhum arquivo de `_fonte/` lido pelos implementadores (clean-room; prompts dos subagentes proíbem)
- [ ] `git ls-files | grep -iE "\.wav|\.onnx|\.zip"` → só fixtures pequenas (< 200 KB)

**Teste rápido por ciclo**: `uv run pytest -q -x`
**Teste completo no final**: `uv run pytest -q && uv run ata doctor --skip-benchmark`

## Restrições
- só pela assinatura (sem API sem autorização); Ollama local permitido no código, mas não executado nos testes
- não mexer em: `~/projetos/crunchlog/_fonte`, instalação do CrunchLog, config `~/.config/crunchlog`
- clean-room: implementar a partir de PESQUISA + ANALISE + PROPOSTA, nunca do código-fonte do CrunchLog

## Portões humanos (parar e perguntar)
- gasto de crédito / API / render pago
- apagar dados
- (push do repo público e portal: autorizados pelo usuário em 05/10 — "publique no portal")
