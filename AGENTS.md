---
title: "Agents Master"
tags: []
---

# AGENTS.md — hale-bopp-marginalia

> Markdown vault quality toolkit: scanner, fixer, linker, evaluator. Python CLI, zero dependencies. Obsidian plugin companion.
> Guardrails e regole: vedi `.cursorrules` nello stesso repo.
> Workspace map: vedi `easyway/infra/factory-vcs.json` (SSoT repo map, branch strategy, deploy metadata).

## Identità
| Campo | Valore |
|---|---|
| Cosa | Python CLI (v1.1.1) — 18 comandi: scan, fix (8 Giri), link, discover, eval, graph, ai, closeout, index, tags, catalog, quickstart, css |
| Linguaggio | Python 3.9+ (stdlib only, zero pip deps), TypeScript (Obsidian plugin) |
| Branch | `feature/* -> develop -> main` (target da `factory-vcs.json`) |
| Test | 128 test, pytest + GitHub Actions CI |
| NPM | Produces npm package for Obsidian plugin component |
| PyPI | `pip install marginalia` (CLI, MIT, Circle 2) |

## Comandi rapidi
```bash
marginalia scan /path/to/vault          # Quality scan
marginalia fix /path/to/vault --apply   # Auto-fix (8 Giri pipeline)
marginalia eval /path/to/vault          # Health snapshot
marginalia link /path/to/vault --min 0.3 # Semantic link suggestions
marginalia discover /path/to/vault      # Hidden connections
marginalia graph /path/to/vault         # Relationship graph JSON
marginalia closeout NNN --vault /path/to/wiki --write  # Session closeout
```

## Struttura
```text
src/marginalia/       # Python CLI core (14 modules)
  fixer.py            # 8-pass pipeline (Giro 0-7)
  linker.py           # TF-IDF semantic linker
  evaluator.py        # Snapshot-based health evaluation
  scanner.py          # Quality scanner (frontmatter, links, sections)
  indexer.py          # MOC + tag index generator
  graph.py            # Multi-layer graph export
  discover.py         # Hidden connection discovery
  ai.py               # LLM-powered analysis (optional)
obsidian-plugin/      # Obsidian companion plugin (TypeScript)
tests/                # 128 pytest tests
docs/                 # Documentation
```

## Regole specifiche
| Regola | Dettaglio |
|---|---|
| Zero dipendenze | Solo stdlib Python. Nessun pip install richiesto per funzionare |
| Idempotenza | `fix --apply` può essere rieseguito senza side-effect |
| Config | `.marginalia.yml` per-vault, comandi override via CLI flags |
| Test | 128 test, CI via GitHub Actions, `npx vitest` per plugin |

## Workflow & Connessioni
| Cosa | Dove |
|---|---|
| ADO operations (WI, PR) | → vedi `easyway-wiki/guides/agents/agent-ado-operations.md` |
| Scheda wiki | `easyway-wiki/repos/marginalia.md` |
| Quickstart guide | `easyway-wiki/guides/marginalia-quickstart.md` |
| Factory VCS | `easyway/infra/factory-vcs.json` (hale-bopp-marginalia, Circle 2) |

---
> Context Sync Engine | Master: `easyway-wiki/templates/agents-master.md`
> Override: `easyway-wiki/templates/repo-overrides.yml` | Sync: 2026-04-29T00:00:00Z
