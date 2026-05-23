# Changelog

All notable changes to **marginalia** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-05-22

### Added

- **`schema` command** — Wiki schema blueprint (Karpathy LLM-Wiki pattern).
  Implements the structure/conventions document that orients agents *before*
  they write into a vault. Three subcommands:
  - `marginalia schema init <vault>` — create `schema.md` template at vault
    root (refuse if exists, `--force` overwrites)
  - `marginalia schema validate <vault>` — verify the schema's claims match
    the actual vault state (declared paths exist, required frontmatter
    present, taxonomy ref resolvable)
  - `marginalia schema show <vault>` — print parsed schema as object
    (supports `--json`)

  This is PBI 1/4 of the LLM-Wiki growth-loop epic (AB#2187). It pairs with
  upcoming `log`, `ingest`, and `query` commands to implement the pattern
  described in Karpathy's gist
  [`442a6bf`](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
  on top of marginalia's existing analytic engine.

  Tracked: AB#2188. Zero-dep, 15 new pure unit tests.

## [1.2.0] — earlier

- EW-aware parser for orphan detection (AB#1966)
- Doc placement enforcement (`types` command, AB#1858)
- Layer classification (`layer classify`/`resolve`)
- 5-domande rubric scan (`--rubric 5d-ew`, AB#1801)
- Nonna Standard guide compliance
- Handoff v2 validator
- Multi-vault scan, TF-IDF linker, discovery, graph-export, eval, AI brain
