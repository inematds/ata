---
name: meeting-prep
description: Monta a cola de preparação (meeting prep / cheatsheet) de uma próxima reunião a partir das anteriores no Ata — ações em aberto, decisões já tomadas, perguntas pendentes e roteiro com itens (qN) marcados ao vivo. Use para "prepara a reunião com X", "o que preciso levar para a call de amanhã".
---

# Preparação de reunião (Ata)

## Passos

1. Pegue o assunto/título (ou o nome de quem vai participar).
2. Prévia: `ata_prep` com `title` e `days` (padrão 90). Mostre o Markdown e os `items` (qN).
3. Contexto extra: `ata_person` para cada participante (reuniões, tempo de fala, ações dele) e `ata_search` com o assunto.
4. Revise com o usuário; ao aprovar, grave com `ata_prep` `save: true` (vai para a pasta de notas).
5. Durante a reunião: `ata live start --prep "<assunto>"` marca os itens (qN) respondidos em `live/prep.json`; `ata live tail` mostra os turnos.
6. Depois, confira o que ficou sem resposta e leve para `action-items`.

CLI: `ata prep "<assunto>" --days 90`.
