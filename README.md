# levi-md

**Markdown vault quality scanner for Obsidian, academics, and documentation teams.**

Zero dependencies. Pure Python. Works on any Markdown vault.

## Install

```bash
pip install levi-md
```

Or from source:

```bash
pip install -e .
```

## Usage

### Scan vault for quality issues

```bash
levi scan ~/my-vault/
levi scan ~/my-vault/ --json
levi scan ~/my-vault/ --require title,tags,status
```

Checks for:
- Missing or incomplete YAML frontmatter
- Empty sections (heading with no content)
- Broken internal links (with "did you mean?" suggestions)
- Broken `[[wikilinks]]`

### Obsidian health check

```bash
levi check ~/my-vault/
```

Checks for:
- `.obsidian/` tracked in git (should be gitignored)
- Missing `.gitignore` with recommended entries
- Directory hierarchy too deep or too flat
- Mixed case directory names
- Unresolved `[[wikilinks]]`
- Accidental `Untitled.canvas` files

### Fix tags (migrate flat to namespaced)

```bash
# Dry run (preview changes)
levi fix-tags ~/my-vault/

# Apply changes
levi fix-tags ~/my-vault/ --apply

# Custom taxonomy
levi fix-tags ~/my-vault/ --taxonomy my-taxonomy.yml
```

### Export relationship graph

```bash
levi graph ~/my-vault/ > graph.json
```

Returns JSON with:
- Tag index (which files use which tags)
- Link graph (who links to whom)
- Topology (hubs, authorities, orphans)
- Tag-based clusters

## Custom taxonomy

Create a YAML file with your own tag namespaces:

```yaml
namespaces:
  course: [math, physics, history, literature, philosophy]
  type: [lecture-notes, essay, bibliography, summary, review]
  status: [draft, revision, final, published]
merges:
  notes: lecture-notes
  bib: bibliography
```

Then: `levi fix-tags ~/vault/ --taxonomy my-taxonomy.yml`

## For whom?

- **Students**: Keep your thesis vault clean — frontmatter, links, tag structure
- **Researchers**: Map knowledge topology across hundreds of notes
- **Documentation teams**: Enforce quality gates on Markdown wikis
- **Obsidian users**: Find broken links, orphan notes, hierarchy issues

## Zero dependencies

levi-md uses only Python standard library. No PyYAML, no external packages. Runs anywhere Python 3.9+ is installed.

## License

MIT
