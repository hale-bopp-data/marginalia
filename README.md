# marginalia

**Markdown vault quality scanner for Obsidian, academics, and documentation teams.**

Zero dependencies. Pure Python. Works on any Markdown vault.

## Install

```bash
pip install marginalia
```

Or from source:

```bash
git clone https://github.com/hale-bopp-data/marginalia
cd marginalia
pip install -e .
```

## Quick start

```bash
marginalia scan ~/my-vault/
marginalia scan ~/my-vault/ --json
```

---

## Commands

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

### `fix` — Automated fixes (4 Giri pipeline)

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
marginalia discover ~/my-vault/ --json
```

Finds clusters of notes with overlapping tags that don't link to each other yet.

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

Requires `OPENROUTER_API_KEY` (or `OPENAI_API_KEY` / `OLLAMA_HOST`). Uses the AI provider configured in `marginalia.yaml`.

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

---

## Zero dependencies

marginalia uses only the Python standard library. No PyYAML, no external packages. Runs anywhere Python 3.9+ is installed.

## Part of HALE-BOPP

> *Sovereign by design. Cloud by choice.*

marginalia is part of the [HALE-BOPP](https://github.com/hale-bopp-data) open-source ecosystem — portable, replicable tools for data and knowledge governance. Your vault quality runs where you decide, not where a vendor tells you.

- [hale-bopp-db](https://github.com/hale-bopp-data/hale-bopp-db) — Schema governance for PostgreSQL
- [hale-bopp-etl](https://github.com/hale-bopp-data/hale-bopp-etl) — Config-driven data orchestration
- [hale-bopp-argos](https://github.com/hale-bopp-data/hale-bopp-argos) — Policy gating and quality checks
- **marginalia** (this repo) — Markdown vault quality scanner

## License

MIT
