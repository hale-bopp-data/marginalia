---
title: "Agents Master"
tags: []
---

# AGENTS.md — hale-bopp-marginalia

> Plugin Obsidian per gestione sessioni AI: apertura, chiusura, context tracking.
> Guardrails e regole: vedi `.cursorrules` nello stesso repo.
> Workspace map: vedi `factory.yml` nella root workspace (mappa completa repos, stack, deploy).

## Identità
| Campo | Valore |
|---|---|
| Cosa | Plugin TypeScript per Obsidian — session management, closeout automation |
| Linguaggio | TypeScript, Python (CLI) |
| Branch | `feat→main` (NO develop) — PR target: `main` |


## Comandi rapidi
```bash
ewctl commit
# Build plugin
cd obsidian-plugin && npm run build
# Run tests
pytest tests/
# Install CLI
pip install -e .
```

## Struttura
```text
src/
  marginalia/        # Python CLI package
obsidian-plugin/     # Obsidian plugin (TypeScript)
tests/               # Test suite
docs/                # Documentation
pyproject.toml       # Python package metadata
```

## Regole specifiche marginalia
| Regola | Dettaglio |
|---|---|
| Plugin | Obsidian: build con esbuild |
| CLI | Python: `marginalia` command per session management |
| Test | plugin in Obsidian dev vault prima di rilasciare |

## Workflow & Connessioni
| Cosa | Dove |
|---|---|
| ADO operations (WI, PR) | → vedi `easyway-wiki/guides/agents/agent-ado-operations.md` |
| PR flusso standard | → vedi `easyway-wiki/guides/polyrepo-git-workflow.md` |
| PAT/secrets/gateway | → vedi `easyway-wiki/guides/connection-registry.md` |
| Branch strategy | → vedi `easyway-wiki/guides/branch-strategy-config.md` |
| Tool unico | `bash /c/old/easyway/agents/scripts/connections/ado.sh` — MAI curl inline, MAI az login |


---
> Context Sync Engine | Master: `easyway-wiki/templates/agents-master.md`
> Override: `easyway-wiki/templates/repo-overrides.yml` | Sync: 2026-03-27T09:00:16Z
