# AGENTS.md — marginalia

> Tool Python CLI per la qualità dei vault Markdown (Obsidian, PhD, team docs).
> Guardrails e regole: vedi `.cursorrules` nello stesso repo.

## Identità
| Campo | Valore |
|---|---|
| Cosa | Python CLI — scan, fix, link, eval per vault Markdown |
| Linguaggio | Python >= 3.9 (zero deps core), TypeScript (Obsidian plugin) |
| Repo | `https://github.com/hale-bopp-data/marginalia` |
| Licenza | MIT |
| Versione | → vedi `marginalia/__init__.py` → `__version__` |
| Package | `pip install -e .` — zero dipendenze esterne obbligatorie |

## Comandi rapidi
```bash
pip install -e .
python -m pytest tests/ -v --tb=short
marginalia scan . --json
marginalia link . --json
cd obsidian-plugin && npm ci && npm run build
ruff check marginalia/ --select=E,F,W --ignore=E501
```

## Struttura
```text
marginalia/              # package Python (11 comandi CLI)
  cli.py, scanner.py, linker.py, config.py, fixer.py
  tags.py, obsidian.py, discovery.py, index_builder.py
  brain.py, eval.py
obsidian-plugin/         # TypeScript plugin Obsidian
tests/                   # pytest suite
.github/workflows/ci.yml
```

## Regole per agenti
| # | Regola |
|---|---|
| 1 | MAI modificare coefficienti scoring in `linker.py` senza test |
| 2 | MAI aggiungere dipendenze esterne ai moduli core — solo stdlib |
| 3 | MAI scrivere file utente senza backup preventivo (`.bak`) |
| 4 | Aggiornare `__version__` in `__init__.py` ad ogni release (semver) |
| 5 | `pytest tests/` deve essere verde prima di PR |
| 6 | Ogni subcommand deve supportare `--json` output |
| 7 | `scan` e `link` accettano `nargs="*"` — path multipli |
| 8 | Dry-run di default — operazioni distruttive richiedono `--apply` |

## Riferimenti
| Cosa | Dove |
|---|---|
| Scoring formula | → vedi `marginalia/linker.py` (cosine + tag_overlap + dir bonus) |
| CLI commands (11) | → vedi `marginalia/cli.py` o `marginalia --help` |
| Config format | → vedi `marginalia.yaml` in qualsiasi vault |
| Eval RAG workflow | → vedi `tests/test_eval.py` per formato snapshot/compare |
| Regola Benchmark S122 | → vedi `easyway-wiki/guides/lessons-learned.md` |
| Convenzioni codice | → vedi `.cursorrules` nello stesso repo |

## Workflow & Connessioni
| Cosa | Dove |
|---|---|
| PR flusso (GitHub-primary) | → vedi `easyway-wiki/guides/polyrepo-git-workflow.md` |
| ADO ↔ GitHub | Ogni commit/PR deve referenziare `AB#NNN` — vedi `.cursorrules` |

---
> Context Sync Engine | Master: `easyway-wiki/templates/agents-master.md`
