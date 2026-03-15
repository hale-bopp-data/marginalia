---
title: "Agents Master"
tags: []
---

# AGENTS.md — hale-bopp-marginalia

> Plugin Obsidian per gestione sessioni AI: apertura, chiusura, context tracking.
> Guardrails e regole: vedi `.cursorrules` nello stesso repo.

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

## ADO Workflow
```bash
# Tool UNICO — MAI curl inline, MAI az login
bash /c/old/easyway/ado/scripts/ado-remote.sh wi-create "titolo" "PBI" "tag1;tag2"
bash /c/old/easyway/ado/scripts/ado-remote.sh pr-create hale-bopp-marginalia <src> main "AB#NNN titolo" NNN
bash /c/old/easyway/ado/scripts/ado-remote.sh pr-autolink-wi <pr_id> hale-bopp-marginalia
bash /c/old/easyway/ado/scripts/ado-remote.sh pat-health-check
```
Repo ADO: `easyway-portal`, `easyway-wiki`, `easyway-agents`, `easyway-infra`, `easyway-ado`, `easyway-n8n`

## PR — Flusso standard
```bash
cd /c/old/easyway/marginalia && git push -u origin feat/nome-descrittivo
bash /c/old/easyway/ado/scripts/ado-remote.sh pr-create hale-bopp-marginalia feat/nome-descrittivo main "AB#NNN titolo" NNN
```


## Connessioni
- **PAT/secrets**: SOLO su server `/opt/easyway/.env.secrets` — MAI in locale
- **Guida**: `easyway-wiki/guides/connection-registry.md`
- **`.env.local`**: solo OPENROUTER_API_KEY e QDRANT

---
> Context Sync Engine | Master: `easyway-wiki/templates/agents-master.md`
> Override: `easyway-wiki/templates/repo-overrides.yml` | Sync: 2026-03-14T21:00:06Z
