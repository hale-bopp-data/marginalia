# marginalia

[![CI](https://github.com/hale-bopp-data/marginalia/actions/workflows/ci.yml/badge.svg)](https://github.com/hale-bopp-data/marginalia/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/marginalia)](https://pypi.org/project/marginalia/)
[![Python](https://img.shields.io/pypi/pyversions/marginalia)](https://pypi.org/project/marginalia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**Markdown vault quality toolkit: scanning, connection discovery, and knowledge graph export for Obsidian, academics, and documentation teams.**

Zero dependencies. Pure Python. Works on any Markdown vault.

## Install

### Prerequisites

You need **Python 3.9 or newer**. Check if you have it:

```bash
python --version
```

If you don't have Python, install it from [python.org/downloads](https://www.python.org/downloads/) — check **"Add Python to PATH"** during installation.

### Install marginalia

```bash
pip install marginalia
```

That's it. Verify it works:

```bash
marginalia scan --help
```

### Install from source (for contributors)

```bash
git clone https://github.com/hale-bopp-data/marginalia
cd marginalia
pip install -e .
```

## Quick start

```bash
marginalia catalog
marginalia quickstart ~/my-vault/ --write
marginalia scan ~/my-vault/
marginalia scan ~/my-vault/ --json
```

If you're new to the tool, start with `catalog` to see the capability map, then run `quickstart` to generate an operator blueprint (`operator-blueprint.json` + `.md`) with the next recommended flow for your vault.

---

## Commands

### `catalog` — Operator capability map

```bash
marginalia catalog
marginalia catalog --json
```

Shows the tool catalog grouped by operator goal: baseline, catalog, normalization, materialization, guardrails, and measurement.

### `quickstart` — Guided operator flow + blueprint materialization

```bash
marginalia quickstart ~/my-vault/
marginalia quickstart ~/my-vault/ --write
marginalia quickstart ~/my-vault/ --write --output out/ops
```

Reads the current vault state, identifies the next best slice, and suggests the operational flow. With `--write`, it materializes:
- `operator-blueprint.json`
- `operator-blueprint.md`

This is the fastest way to answer: "what should I run next on this vault?"

### `scan` — Quality scan

```bash
marginalia scan ~/my-vault/
marginalia scan ~/my-vault/ --require title,tags,status
marginalia scan docs/ ../wiki/          # multi-vault
marginalia scan ~/my-vault/ --json
```

Checks for:
- Missing or incomplete YAML frontmatter (`title`, `tags`, `status`, …)
- Empty sections — heading-hierarchy-aware: ignores sections with sub-headings, code blocks, templates, and archive files
- Broken internal links — with "did you mean?" suggestions
- Broken `[[wikilinks]]`

Add `--tag` to auto-tag files with issues for Obsidian review (see `scan --tag` below).

### `link` — TF-IDF link suggestions

```bash
marginalia link ~/my-vault/             # preview suggestions
marginalia link ~/my-vault/ --apply     # write ## See also sections
marginalia link docs/ ../wiki/ --min-score 0.3 --max-links 5
```

Finds semantically related notes using TF-IDF cosine similarity. Scores boost notes that share tags or directories. Writes `## See also` sections with relative `[[wikilinks]]` (dry-run by default).

### `fix` — Automated fixes (8 Giri pipeline, Giro 0-7)

```bash
marginalia fix ~/my-vault/              # dry-run
marginalia fix ~/my-vault/ --apply --no-what-if
```

Four passes: normalise frontmatter → fix broken links → clean empty sections → standardise headings.

### `fix-tags` — Migrate flat tags to namespaced

```bash
marginalia fix-tags ~/my-vault/                     # dry-run
marginalia fix-tags ~/my-vault/ --apply
marginalia fix-tags ~/my-vault/ --taxonomy my.yml   # custom taxonomy
```

### `tags` — Tag Dictionary & Inventory (L0)

```bash
# Fast: read existing frontmatter, detect synonyms by pattern
marginalia tags ~/my-vault/
marginalia tags ~/my-vault/ --out tag-dictionary.json

# Full: LLM reads each page, suggests tags with reasoning
marginalia tags ~/my-vault/ --analyze --out tag-inventory.json
marginalia tags ~/my-vault/ --analyze --taxonomy taxonomy.yml --out tag-inventory.json
```

**Fast mode** (default): reads existing frontmatter tags, counts usage, detects synonym candidates by name similarity.

**Analyze mode** (`--analyze`): for each page, the LLM reads the content and suggests tags with **reasoning** (why this tag?). The inventory records `{file, existing_tags, suggested: [{tag, reason}]}` for every page. Tags with similar reasons across different pages = synonyms.

**Rationalize mode** (`--rationalize`): the LLM sees the *full* tag landscape across all files and proposes merges — non-canonical domains → canonical, zombie namespaces → canonical, flat tags → namespaced. Returns proposed YAML merges ready to paste into the taxonomy.

Designed for a 3-level tag lifecycle:
1. `tags --analyze` (L0) — per-page inventory with reasoning ("what's there and why?")
2. `tags --rationalize` (L0→L1) — global rationalization across all tags
3. Edit taxonomy YAML (L1) — curate synonyms in `merges:` section
4. `fix-tags --taxonomy` (L2) — apply normalisation across the vault

### `scan --tag` + `untag` — Obsidian review workflow

```bash
# Tag files with issues so you can find them in Obsidian
marginalia scan ~/my-vault/ --tag

# In Obsidian: search  tag:quality/review-needed

# After manual review, remove the tags
marginalia untag ~/my-vault/ --apply
```

Every `scan` with issues prints an Obsidian tip at the end showing how to find affected files.

### `check` — Obsidian health check

```bash
marginalia check ~/my-vault/
```

Detects: `.obsidian/` tracked in git, missing `.gitignore`, hierarchy too deep/flat, mixed-case dirs, unresolved `[[wikilinks]]`, accidental `Untitled.canvas`.

### `discover` — Hidden connections

```bash
marginalia discover ~/my-vault/
marginalia discover ~/my-vault/ --json
marginalia discover ~/my-vault/ --min-tags 3 --max-results 100
```

Analyzes your vault to find connections you didn't know you had:

- **Tag affinity** — pairs of files that share multiple tags but aren't linked. These are "hidden connections": notes about the same topic the author never explicitly connected.
- **Orphan rescue** — orphan files that *should* be linked from somewhere, with a suggested parent and confidence score.
- **Cluster bridges** — hub documents that span multiple tag domains, useful for connecting separate clusters.

Human-readable output shows top 10 per category with details; `--json` returns the full dataset for programmatic use.

### `index` — Generate MOC + indexes

```bash
marginalia index ~/my-vault/
```

Outputs: Map of Content (MOC), tag index, orphan index.

### `graph` — Link graph JSON

```bash
marginalia graph ~/my-vault/ > graph.json
```

Returns: tag index, link graph, topology (hubs, authorities, orphans), tag clusters.

### `graph-export` — Knowledge graph for RAG expansion

```bash
marginalia graph-export ~/my-vault/                        # writes wiki-graph.json
marginalia graph-export ~/my-vault/ -o custom-graph.json   # custom output path
marginalia graph-export ~/my-vault/ --json                 # print to stdout
marginalia graph-export ~/my-vault/ --min-tags 3 --top-k 10 --min-similarity 0.4
```

Exports a consolidated knowledge graph that combines **four relationship layers** into a single `wiki-graph.json`:

| Layer | Source | What it captures |
|-------|--------|------------------|
| **Link graph** | Scanner | Explicit markdown links and `[[wikilinks]]` between documents |
| **Tag affinity** | Discovery | Implicit relationships via shared tags (files about the same topic that aren't linked) |
| **Cluster bridges** | Discovery | Hub documents that span multiple tag domains |
| **TF-IDF similarity** | Linker | Semantic neighbors by content similarity |

The output includes backlinks (reverse index), per-file tag index, and metadata with edge counts.

**Options:**
- `--min-tags N` — minimum shared tags for affinity pairs (default: 2)
- `--top-k N` — top-K similar documents per file (default: 5)
- `--min-similarity F` — minimum TF-IDF cosine similarity score (default: 0.35)

**Use case:** Feed `wiki-graph.json` to any RAG pipeline that supports graph expansion. After initial retrieval, walk the graph to add related documents to the LLM context — turning isolated search results into context-aware answers.

### `css` — Tag colour snippets for Obsidian

```bash
marginalia css ~/my-vault/ > tags.css
```

### `ai` — AI-powered analysis

```bash
marginalia ai review ~/my-vault/
marginalia ai tag ~/my-vault/
marginalia ai connect ~/my-vault/
marginalia ai frontmatter ~/my-vault/
```

Requires an API key for any OpenAI-compatible provider (see [LLM Configuration](#llm-configuration) below).

### `eval` — Before/after RAG quality measurement

```bash
# Build a snapshot of current retrieval quality
marginalia eval snapshot ~/my-vault/ queries.yaml before.json

# After vault changes, take another snapshot
marginalia eval snapshot ~/my-vault/ queries.yaml after.json

# Compare
marginalia eval compare before.json after.json
```

Measures: `top1_score`, `coverage`, `precision@K`, `recall@K`. Verdict: `IMPROVED / DEGRADED / NEUTRAL`.

**queries.yaml format:**
```yaml
queries:
  - text: "deploy to production"
    expected:
      - deploy.md
  - text: "semantic search qdrant"
    # no expected — coverage only
```

---

## Config file

`marginalia.yaml` is auto-discovered in the current directory or vault root:

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

## Custom taxonomy

```yaml
namespaces:
  course: [math, physics, history, literature, philosophy]
  type: [lecture-notes, essay, bibliography, summary, review]
  status: [draft, revision, final, published]
merges:
  notes: lecture-notes
  bib: bibliography
```

Then: `marginalia fix-tags ~/vault/ --taxonomy my-taxonomy.yml`

---

## Obsidian plugin

An Obsidian plugin is available in `obsidian-plugin/`. It wraps the CLI with ribbon buttons and a results panel.

**Build:**
```bash
cd obsidian-plugin
npm ci
npm run build
```

Copy `main.js`, `manifest.json`, `styles.css` to your vault's `.obsidian/plugins/marginalia/`.

---

## For whom?

- **Students** — Keep your thesis vault clean: frontmatter, links, tag structure
- **Researchers** — Map knowledge topology across hundreds of notes
- **Documentation teams** — Enforce quality gates on Markdown wikis
- **Obsidian users** — Find broken links, orphan notes, hierarchy issues, get automatic link suggestions
- **AI/RAG builders** — Export a multi-layer knowledge graph (`graph-export`) to power context-aware retrieval pipelines

---

## LLM Configuration

AI-powered commands (`ai`, `tags --analyze`, `closeout --ai`) require an API key. Set one of these environment variables:

| Variable | Provider | Base URL (auto) |
|----------|----------|-----------------|
| `MARGINALIA_API_KEY` | Any OpenAI-compatible | Set `MARGINALIA_API_URL` too |
| `OPENROUTER_API_KEY` | OpenRouter (default) | `https://openrouter.ai/api/v1` |
| `DEEPSEEK_API_KEY` | DeepSeek | `https://api.deepseek.com` |
| `OPENAI_API_KEY` | OpenAI | `https://api.openai.com/v1` |

Optional:
- `MARGINALIA_MODEL` — model name (default: `deepseek/deepseek-chat`)
- `MARGINALIA_API_URL` — custom base URL (e.g., `http://localhost:11434/v1` for Ollama)

All AI features are optional — marginalia works fully without any API key.

---

## Testing

marginalia ships with 128 tests covering all core modules.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test module
python -m pytest tests/test_scanner.py -v
python -m pytest tests/test_linker.py -v
python -m pytest tests/test_fixer_giro7.py -v
```

### Test coverage

| Module | Tests | What's covered |
|--------|-------|----------------|
| `test_scanner.py` | Frontmatter parsing, tag extraction, broken link detection, empty sections, wikilinks, Giro 7 quality checks (summary_todo, stale_draft, empty_required_fields) |
| `test_linker.py` | TF-IDF vectorization, cosine similarity, relative link computation, tag overlap scoring |
| `test_fixer_giro7.py` | 4-pass fixer pipeline, stale draft rules (path-based auto-resolution), frontmatter normalization |
| `test_eval.py` | RAG quality snapshots, before/after comparison, precision/recall metrics |
| `test_closeout.py` | Git data collection, session template generation |
| `test_validators.py` | YAML validation, taxonomy checks, retry logic |

All tests are pure unit tests — no network, no filesystem side effects, no external services required.

---

## Zero dependencies

marginalia uses only the Python standard library. No PyYAML, no external packages. Runs anywhere Python 3.9+ is installed.

## Origin story

marginalia was born inside [EasyWay](https://github.com/hale-bopp-data), a data governance platform with 50+ AI agents and a growing wiki of 500+ Markdown files. As the wiki scaled, quality eroded: broken links, orphan pages, inconsistent tags, missing frontmatter. Manual reviews couldn't keep up.

We built marginalia to automate what humans forget: **find every broken link, detect every missing tag, suggest every connection**. Within days of deploying it, EasyWay's wiki went from 40% frontmatter coverage to 98%, broken links dropped to zero, and the tag taxonomy became consistent across all documentation.

The tool turned out to be useful far beyond our project — any Obsidian vault, research wiki, or documentation repo has the same problems. So we extracted it, removed all internal dependencies, and released it as a standalone product.

**What it did for us, it can do for you.**

## Part of HALE-BOPP

> *Sovereign by design. Cloud by choice.*

marginalia is part of the [HALE-BOPP](https://github.com/hale-bopp-data) open-source ecosystem — portable, replicable tools for data and knowledge governance. Your vault quality runs where you decide, not where a vendor tells you.

- [hale-bopp-db](https://github.com/hale-bopp-data/hale-bopp-db) — Schema governance for PostgreSQL
- [hale-bopp-etl](https://github.com/hale-bopp-data/hale-bopp-etl) — Config-driven data orchestration
- [hale-bopp-argos](https://github.com/hale-bopp-data/hale-bopp-argos) — Policy gating and quality checks
- **marginalia** (this repo) — Markdown vault quality toolkit and knowledge graph builder

## License

MIT
