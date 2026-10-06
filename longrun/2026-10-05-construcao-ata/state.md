# Estado
- Fase: construção paralela A–E (subagentes rodando).
- Próximo: integrar relatórios, rodar suíte inteira, corrigir costuras, critérios do goal.md, README/guia trilíngue, repo público inematds/ata, portal.
- 2026-10-05 ~21h: LIMITE DE USO atingido. Bloco E falhou no meio (dashboard: faltava offsets em meeting_detail). A–D possivelmente incompletos. Commit WIP feito. Retomar: rodar `uv run pytest -q`, ver o que falta por bloco (INTERFACES.md), relançar só o que faltar.
- Bloco B CONCLUÍDO (108 testes pipeline+core). Notas de integração no relatório: kwargs extras gate/turns/render_note; process em crunchlog/2 copia p/ ata/1.
- Bloco A CONCLUÍDO (81 testes + linux_audio real ok). start(backend=nome); stop bloqueia até processar; exceções AlreadyRecording/NothingRecording/CaptureMissing.
- Bloco C CONCLUÍDO (92 testes). API do NeMo-Speech.cpp é suposição documentada em nemo.py; sha256 vazios no models.toml (install exige --allow-unverified).
- Bloco D CONCLUÍDO (103 testes). Pendências vistas: 2 falhas em tests/engines nemo (C) na suíte completa, 2 no MCP do E (KeyError ata.pipeline no conftest dele). E falta: demo, revisar mcp/dashboard/live.
