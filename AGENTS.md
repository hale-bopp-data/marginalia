# AGENTS.md — marginalia

Istruzioni operative per agenti AI (Codex, Claude Code, Copilot Workspace, ecc.)
che lavorano in questo repository.

---

## Identità del progetto

**marginalia** è un tool Python CLI per la qualità dei vault Markdown (Obsidian, PhD, team docs).
- Repo: `https://github.com/hale-bopp-data/marginalia`
- Licenza: MIT
- Versione corrente: `marginalia/__init__.py` → `__version__`
- Package: `pip install -e .` — zero dipendenze esterne obbligatorie

---

## Struttura repository

```
marginalia/              ← package Python
  __init__.py            ← versione
  cli.py                 ← entrypoint CLI (11 comandi)
  scanner.py             ← find_md_files, scan_file, build_graph
  linker.py              ← TF-IDF cosine similarity, build_suggestions, run_link
  config.py              ← loader marginalia.yaml (zero deps)
  fixer.py               ← 4 Giri fix pipeline
  tags.py                ← taxonomy, tag migration
  obsidian.py            ← Obsidian health checks
  discovery.py           ← hidden connection discovery
  index_builder.py       ← MOC, tag index, orphan index
  brain.py               ← AI analysis (OpenRouter/OpenAI/Ollama)
  eval.py                ← before/after RAG quality measurement
obsidian-plugin/         ← TypeScript plugin Obsidian
  src/main.ts            ← plugin logic
  manifest.json
  package.json
tests/
  test_linker.py         ← pytest test suite
  test_eval.py           ← eval snapshot/compare tests
.github/workflows/
  ci.yml                 ← CI: test Python 3.9-3.12, lint, build plugin
pyproject.toml
```

---

## Comandi operativi

### Setup
```bash
pip install -e .
```

### Test
```bash
python -m pytest tests/ -v --tb=short
```

### Smoke test rapido
```bash
marginalia scan . --json
marginalia link . --json
```

### Build plugin Obsidian
```bash
cd obsidian-plugin && npm ci && npm run build
```

### Lint
```bash
ruff check marginalia/ --select=E,F,W --ignore=E501
```

---

## Convenzioni di codice

- **Python >= 3.9** — type hints ok, `|` union syntax (3.10+) da evitare nei moduli core
- **Zero dipendenze esterne** per i moduli core (scanner, linker, config, fixer, tags, obsidian)
  - `brain.py` usa urllib stdlib (non requests)
  - Il plugin TypeScript usa solo `obsidian` (peer dep) e `esbuild`
- **Nessun PyYAML** — config.py usa il parser minimale interno `_parse_yaml()`
- **UTF-8 ovunque** — `encoding="utf-8", errors="replace"` su tutti i `read_text()`
- **Dry-run di default** — ogni operazione distruttiva richiede `--apply` + `--no-what-if`
- **Backup sempre** prima di modificare file utente (vedi `run_link` apply phase)

---

## Scoring formula (linker.py)

```
score = cosine_similarity
      + (0.08 × tag_overlap)
      + (0.04 × same_dir)
      + (0.02 × same_top_dir)
```

Non modificare i coefficienti senza aggiornare i test in `test_linker.py`.

---

## CLI commands

| Comando | Descrizione |
|---|---|
| `scan [VAULT...]` | Scan qualità frontmatter/link/sezioni vuote |
| `check [vault]` | Obsidian health checks |
| `fix [vault]` | 4 Giri fix pipeline (dry-run default) |
| `fix-tags [vault]` | Migrazione tag flat→namespaced |
| `discover [vault]` | Connessioni nascoste per tag/topologia |
| `index [vault]` | Genera MOC, tag index, orphan index |
| `css [vault]` | CSS snippet colori tag per Obsidian |
| `graph [vault]` | Link graph JSON |
| `link [VAULT...]` | TF-IDF link suggestions + apply |
| `ai <action> [vault]` | AI review/tag/connect/frontmatter |
| `eval snapshot VAULT QUERIES OUT` | Snapshot qualità RAG (TF-IDF) |
| `eval compare BEFORE AFTER` | Diff due snapshot → verdict IMPROVED/DEGRADED/NEUTRAL |

---

## Config file (marginalia.yaml)

Auto-scoperto in cwd o nella vault dir. Esempio:

```yaml
vaults:
  - docs/
  - ../wiki/
exclude:
  - node_modules/
  - .git/
  - archive/
min_score: 0.35
max_links: 5
top_k: 7
heading: "## See also"
```

---

## Regole per agenti

1. **MAI modificare i coefficienti di scoring** senza test che lo giustifichino.
2. **MAI aggiungere dipendenze esterne** ai moduli core — usare solo stdlib.
3. **MAI scrivere file utente** senza backup preventivo (pattern: `.bak` in run-dir).
4. **Aggiornare `__version__`** in `__init__.py` ad ogni release (semver).
5. **Test prima di PR** — `pytest tests/` deve essere verde.
6. **`--json` su tutti i comandi** — ogni subcommand deve supportare output JSON machine-readable.
7. **Multi-vault**: `scan` e `link` accettano `nargs="*"` — i path si passano come argomenti posizionali multipli.
8. **eval**: usa il motore TF-IDF interno (zero deps). Il file queries è YAML o JSON. Il formato snapshot è v1 JSON con `version`, `createdAt`, `vaultPaths`, `docs`, `summary`, `queries`.

---

## eval — Before/After RAG Quality

### Queries file (YAML)

```yaml
queries:
  - text: "deploy to production"
    expected:
      - deploy.md
  - text: "secrets PAT rotation"
    expected:
      - secrets.md
  - text: "semantic search"   # no expected — solo coverage
```

### Workflow tipico

```bash
# Prima del refactoring vault
marginalia eval snapshot ./vault queries.yaml before.json --top-k 5

# Dopo il refactoring
marginalia eval snapshot ./vault queries.yaml after.json --top-k 5

# Confronto
marginalia eval compare before.json after.json
```

### Output compare (JSON)

```json
{
  "aggregate": {
    "verdict": "IMPROVED",
    "avg_top1_score_delta": 0.12,
    "coverage_delta": 0.25
  },
  "queries": [
    {
      "text": "deploy to production",
      "top1_score_delta": 0.15,
      "new_results": ["guides/deploy-v2.md"],
      "lost_results": []
    }
  ]
}
```

### Metriche

| Metrica | Significato |
|---|---|
| `top1_score` | Score del primo risultato |
| `coverage` | Frazione query con ≥1 risultato sopra min_score |
| `precision_at_k` | Hit / K (solo se `expected` forniti) |
| `recall_at_k` | Hit / len(expected) |
| `verdict` | IMPROVED (Δ>0.05) / DEGRADED (Δ<-0.05) / NEUTRAL |

---

## ADO ↔ GitHub — Regola `AB#`

Ogni commit e PR su GitHub (`hale-bopp-data/marginalia`) deve referenziare il Work Item ADO:

```bash
git commit -m "feat: add eval command AB#1234"
# oppure nel body della PR: AB#1234
```

ADO mostra automaticamente il link alla PR/commit GitHub sul WI.
**MAI creare PR senza Work Item ADO** — vale anche su GitHub (Regola del Palumbo).
