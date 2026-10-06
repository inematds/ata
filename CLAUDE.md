# CLAUDE.md — ata

Notetaker de reuniões local-first (pt-BR/en/es) do INEMA. Implementação independente inspirada nas ideias do CrunchLog.

- **Clean-room:** nunca abrir/copiar `~/projetos/crunchlog/_fonte/` (código licenciado). Ideias e formato de dados, sim; código, não.
- Contrato: `docs/CONTRATO.md` (bundle `ata/1`, protocolos, config, saídas). Interfaces entre módulos: `docs/INTERFACES.md`. Pesquisa/proposta: `docs/pesquisa/`.
- Testes: `uv run pytest -q` (sem rede, sem modelos; `tests/conftest.py` isola HOME e força `ATA_ENGINES=fake`). Gravação real: `-m linux_audio`.
- Nenhuma API paga: resumo/embeddings via Ollama local; `claude`/`codex` só pela assinatura (CLI, privacy.level=1).
- Motores reais (NeMo-Speech.cpp, onnx, Ollama) implementados mas NÃO medidos na 0.1.0; API do NeMo é suposição documentada em `src/ata/engines/nemo.py`; `data/models.toml` sem sha256.
- Dashboard/servidores de teste: conferir porta com `ss -ltn` antes e matar só o PID que subiu.
- Guia em `guia/` (PT) + `guia/en/` + `guia/es/`; Pages via Actions. Push do workflow: SSH (`inematds` sem escopo workflow).
- Autor dos commits: `inematds <inematds@gmail.com>`. Versão semver (ver regra global).

## Self-learning

When I correct you, or you catch yourself making a mistake: before continuing, add the lesson as a one-line rule under ## Lessons, so it never happens again.

## Lessons

- Saída de `uv run ata dashboard > arquivo` fica em buffer: o link com token não aparece no log; ler `<cache>/dashboard-token`. (05/10/2026)
