# Canal — construcao-ata (só acrescentar; nunca reescrever)

Conhecimento do projeto que a compactação perde: fatos descobertos, aprendizados, glossário, armadilhas, onde estão as coisas.
Não é estado da tarefa (isso vai em state/plan/progress). Preencha cedo — o hook avisa na faixa 1 (~50% do contexto).

Formato: `- AAAA-MM-DD HH:MM · fato|aprendizado|glossário|armadilha · texto`


## 2026-10-05 — início (sessão principal)
- Fonte do desenho: ~/projetos/crunchlog/pesquisa/{PESQUISA-ESTADO-DA-ARTE,ANALISE-CRUNCHLOG,PROPOSTA-PRODUTO-SUPERIOR}.md (Fable, esforço alto).
- Decisões (usuário 05/10): recriar do zero (clean-room, repo público MIT), nome Ata, NeMo-Speech.cpp primário + onnx fallback, Ollama resumo padrão, voiceprints opt-in, TODAS as fases, NÃO testar nos modelos locais (só fakes), publicar no portal.
- Núcleo escrito na sessão principal (commit 4ed2563): types, i18n, config, audio, bundle (ata/1), engines/{base,fake,registry}, testing.synth_meeting, cli (COMMAND_MODULES), conftest (HOME isolado + ATA_ENGINES=fake).
- Blocos em paralelo por subagentes: A captura+recorder · B pipeline+nota+compat · C motores reais+engine/doctor/bench/setup · D conhecimento (resumo, índice, ask, prep, voices, connect) · E MCP, dashboard, live, demo, skills.
- Armadilhas: clean-room — nenhum agente abre crunchlog/_fonte; cli.py importa TODOS os módulos de COMMAND_MODULES (faltando um, o `ata --help` quebra até a integração).
