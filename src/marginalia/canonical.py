"""Canonical source discovery — map entity -> authoritative file.

For each cluster of files sharing a filename prefix, identifies the
canonical source (SSoT) using:

- In-degree (backlinks count) -- more linked-to = more authoritative
- Filename length (fewer segments = more foundational)
- Frontmatter status (active > draft > archived)

Consumed by RAG rerankers to boost canonical chunks for queries
about the entity, preventing source fabrication where a generic-named
file ("components.md", "architecture.md") would otherwise be cited
for a topic it does not discuss.
"""

from pathlib import Path
from collections import defaultdict

from .scanner import find_md_files, parse_frontmatter


PREFIX_STOPWORDS = {
    "adr", "pbi", "bug", "epic", "sprint", "session",
    "the", "how", "what", "why", "when", "where", "can",
    "wiki", "doc", "docs", "readme", "index",
    "agent", "guide", "guides",
    "todo", "draft", "notes", "note",
    "en", "it",
    "case", "lesson", "brief",
}


def _normalize_entity(name):
    return name.lower().strip().replace("_", "-")


def _file_prefix(rel_path):
    """Return entity prefix from filename, or None if not entity-like.

    guides/caronte-bridge.md -> "caronte"
    guides/valentino/components.md -> None (no dash in filename)
    guides/adr-0001-foo.md -> None (stopword prefix)
    """
    stem = Path(rel_path).stem
    if "-" not in stem:
        return None
    prefix = _normalize_entity(stem.split("-", 1)[0])
    if len(prefix) < 3:
        return None
    if prefix in PREFIX_STOPWORDS:
        return None
    return prefix


def _score_file(rel_path, backlinks, file_frontmatter):
    """Composite score: higher = more canonical within cluster."""
    stem = Path(rel_path).stem
    parts = stem.split("-")

    in_degree = len(backlinks.get(rel_path, []))
    length_penalty = len(parts)

    fm = file_frontmatter.get(rel_path) or {}
    status = str(fm.get("status", "")).lower()
    if status == "active":
        status_bonus = 1.0
    elif status in ("", "draft"):
        status_bonus = 0.5
    else:
        status_bonus = 0.2

    return (in_degree * 2.0) - (length_penalty * 0.5) + status_bonus


def build_canonical_sources(
    vault_path,
    *,
    backlinks=None,
    file_frontmatter=None,
    min_cluster_size=2,
    max_entities=500,
    secondary_cap=10,
):
    """Discover canonical sources per entity.

    Clusters files by filename prefix (before first dash). For clusters
    of size >= min_cluster_size, ranks files by composite score and
    designates primary + secondary.

    Parameters:
        vault_path: root of the wiki vault
        backlinks: {rel_path: [source_rel_path, ...]} — optional; caller
                   usually passes the backlinks dict already built in
                   graph_export to avoid double scanning
        file_frontmatter: {rel_path: {...yaml fields...}} — optional;
                          rebuilt from vault if not provided
        min_cluster_size: skip single-file "clusters" (no canonicality
                          question with one file)
        max_entities: output cap (retains top by primary_score)
        secondary_cap: per-entity cap on secondary list

    Returns:
        {
          entity: {
            "primary": rel_path,
            "secondary": [rel_path, ...],
            "cluster_size": N,
            "primary_score": float,
            "detection_method": "filename-prefix"
          }
        }
    """
    base = Path(vault_path)
    backlinks = backlinks or {}

    if file_frontmatter is None:
        file_frontmatter = {}
        for f in find_md_files(base):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                fm = parse_frontmatter(content) or {}
                rel = str(f.relative_to(base)).replace("\\", "/")
                file_frontmatter[rel] = fm
            except Exception:
                continue

    clusters = defaultdict(list)
    for rel in file_frontmatter.keys():
        entity = _file_prefix(rel)
        if entity is None:
            continue
        clusters[entity].append(rel)

    canonical = {}
    for entity, files in clusters.items():
        if len(files) < min_cluster_size:
            continue
        scored = [(f, _score_file(f, backlinks, file_frontmatter)) for f in files]
        scored.sort(key=lambda x: (-x[1], len(x[0]), x[0]))

        primary, primary_score = scored[0]
        secondary = [s[0] for s in scored[1:1 + secondary_cap]]

        canonical[entity] = {
            "primary": primary,
            "secondary": secondary,
            "cluster_size": len(files),
            "primary_score": round(primary_score, 3),
            "detection_method": "filename-prefix",
        }

    if len(canonical) > max_entities:
        top = sorted(canonical.items(), key=lambda x: -x[1]["primary_score"])[:max_entities]
        canonical = dict(top)

    return canonical
