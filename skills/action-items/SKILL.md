---
name: action-items
description: Lista e acompanha as ações (action items) e decisões entre reuniões do Ata, agrupadas por responsável e com prazo. Use para "o que ficou pendente", "minhas tarefas das reuniões", "open action items", "decisões da semana".
---

# Ações e decisões (Ata)

## Passos

1. Ações em aberto: `ata_actions` com `status: "open"` (e `since: "7d"` ou `"2w"` para uma janela; `owner` para uma pessoa). Pagine com `cursor`.
2. Decisões: `ata_decisions` com o mesmo `since`.
3. Agrupe por `owner`; ordene por `due` quando houver; mostre `[title, ts]` como citação.
4. Ação sem dono ou sem prazo: marque "sem responsável"/"sem prazo" — não invente.
5. Quando o usuário disser que concluiu algo, marque com `ata_action_done` usando o `id` do item (`done: false` reabre).
6. Para conferir o contexto de um item, `ata_read` da reunião com `from_s`/`to_s` em volta do `t`.

CLI: `ata actions --since 7d`, `ata actions --decisions`.
