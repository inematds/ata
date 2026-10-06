---
name: meeting-notes
description: Lê uma reunião gravada pelo Ata e entrega um resumo fiel com citações [título, mm:ss] (meeting notes, summary, transcript). Use quando pedirem "resume a reunião", "o que foi falado na call", "notes from the meeting".
---

# Notas de reunião (Ata)

Fonte de verdade: os arquivos do bundle (nota, `summary.json`, `turns.json`). Nunca invente falas.

## Passos

1. Ache a reunião: `ata_list` (filtros `query`, `date_from`, `participant`; pagine com `cursor`). Sem pista, use `meeting_id: "latest"`.
2. Leia o resumo: `ata_read` com `parts: ["meta", "summary"]`.
3. Se o resumo faltar ou for raso, leia a transcrição em páginas: `ata_read` com `parts: ["transcript"]`, `limit: 200` e o `next_cursor` até acabar (ou recorte com `from_s`/`to_s`).
4. Escreva no idioma da reunião (`meta.language`): TL;DR (2 frases), Tópicos, Decisões, Ações (responsável + prazo), Perguntas em aberto.
5. Cite cada item como `[título, mm:ss]` usando `start` do turno ou `evidence[].t`.
6. Falante com rótulo genérico ("Pessoa 2")? Pergunte o nome e aplique com `ata_rename_speakers`.

Sem MCP: `ata export latest --format txt` e `cat` da nota em `paths.notes`.

Privacidade: o conteúdo é do usuário; não copie a transcrição para outros serviços sem ele pedir.
