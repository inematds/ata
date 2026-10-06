---
name: ask-meetings
description: Responde perguntas sobre o que foi dito em reuniões gravadas pelo Ata, sempre com evidência citada [título, mm:ss] (ask meetings, search transcripts). Use para "o que decidimos sobre X?", "quem falou de Y?", "when did we discuss Z".
---

# Perguntar às reuniões (Ata)

## Passos

1. `ata_ask` com a `question` (e `meeting_id` se for de uma reunião só). Use as `evidence` devolvidas como base.
2. Se vier pouca evidência, complemente com `ata_search` (`mode: "hybrid"`; tente também `"lexical"` com termos exatos; filtre por `speaker` ou `date_from`).
3. Abra o trecho em volta para confirmar: `ata_read` com `parts: ["transcript"]`, `from_s = start - 30`, `to_s = start + 60`.
4. Responda curto, no idioma da pergunta, citando cada afirmação `[título, mm:ss]`.
5. Se as reuniões não respondem, diga isso — não complete com suposição.

CLI: `ata ask "pergunta"`, `ata search "termos" --mode lexical`.
