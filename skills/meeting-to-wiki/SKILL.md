---
name: meeting-to-wiki
description: Leva uma reunião do Ata para o segundo cérebro / wiki (Obsidian, Cérebro astra, Agentic OS) — página de fonte, decisões no registro, ações por projeto e links [[pessoa]]. Use para "manda essa reunião para o cérebro", "atualiza a wiki com a call", "meeting to wiki".
---

# Reunião para a wiki (Ata)

## Passos

1. Escolha a reunião: `ata_list` (ou `latest`) e leia `ata_read` com `parts: ["meta", "summary", "note"]`.
2. Confirme os nomes dos falantes; rótulos genéricos viram nomes com `ata_rename_speakers` antes de publicar.
3. Conexão já configurada? Rode `ata connect cerebro --status` (ou `obsidian --status`). Se sim, a nota já é sincronizada ao fim do processamento; para refazer, `ata rerender <id>`.
4. Sem conexão: proponha `ata connect cerebro --dir <pasta> --dry-run` (ou `obsidian --vault <pasta>`), mostre o que seria escrito e só rode sem `--dry-run` com o ok do usuário.
5. Escrita manual (quando pedirem): página `fontes/AAAA-MM-DD-slug.md` com frontmatter `tipo: fonte`, `subtipo: reuniao`, `participants: ["[[nome]]"]`; decisões em `decisoes/registro.md` (acrescentar, nunca reescrever); ações em `projetos/<nome>/acoes.md`.
6. Cada item leva a citação `[título, mm:ss]` e o link para a nota original.

Nunca apague nem reescreva páginas existentes sem pedir.
