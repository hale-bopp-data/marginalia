"""
graph_export.py — Export wiki knowledge graph for RAG expansion.

Combines:
  1. Link graph (scanner) — explicit markdown/wikilinks between documents
  2. Tag affinity (discovery) — implicit relationships via shared tags
  3. Cluster bridges (discovery) — multi-domain hub documents
  4. TF-IDF similarity (linker) — semantic neighbors by content

Output: wiki-graph.json consumable by Alfred OODA for Graph RAG expansion.

PBI #971 — Graph RAG: Marginalia + Cartografo + Hybrid Search
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .scanner import build_file_index, build_graph, find_md_files, parse_frontmatter, extract_tags
from .discovery import discover_tag_affinity, discover_cluster_bridges
from .linker import build_suggestions


def export_wiki_graph(vault_path, *, min_shared_tags=2, top_k_similar=5,
                      min_similarity=0.35, max_affinity=50, max_bridges=20):
    """Build a consolidated wiki graph combining all Marginalia relationship layers.

    Returns a dict ready to be serialized as wiki-graph.json.
    """
    base = Path(vault_path)
    file_index = build_file_index(base)

    # Layer 1: Link graph (explicit markdown + wikilinks)
    graph_data = build_graph(vault_path, file_index=file_index)
    link_graph = graph_data.get("link_graph", {})

    # Build backlinks (reverse index)
    backlinks = {}
    for source, targets in link_graph.items():
        for target in targets:
            backlinks.setdefault(target, []).append(source)

    # Layer 2: Tag affinity (implicit — shared tags, no explicit link)
    tag_affinity = discover_tag_affinity(
        vault_path, file_index=file_index,
        min_shared_tags=min_shared_tags, max_results=max_affinity,
    )

    # Layer 3: Cluster bridges (multi-domain hub docs)
    cluster_bridges = discover_cluster_bridges(
        vault_path, file_index=file_index, max_results=max_bridges,
    )

    # Layer 4: TF-IDF similarity (semantic neighbors)
    similarity_raw = build_suggestions(vault_path, top_k=top_k_similar)
    similarity = {}
    for doc in similarity_raw:
        path = doc.get("path", "")
        sims = [
            {"path": s["path"], "score": round(s["score"], 4), "tag_overlap": s.get("tag_overlap", 0)}
            for s in doc.get("suggestions", [])
            if s.get("score", 0) >= min_similarity
        ]
        if sims:
            similarity[path] = sims

    # Build file→tags index (compact)
    file_tags = {}
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content)
            if fm:
                rel = str(f.relative_to(base)).replace("\\", "/")
                tags = extract_tags(fm)
                if tags:
                    file_tags[rel] = tags
        except Exception:
            pass

    # Metadata
    meta = {
        "version": "1.0.0",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_path": str(base),
        "files_scanned": len(file_tags),
        "link_graph_edges": sum(len(v) for v in link_graph.values()),
        "backlink_edges": sum(len(v) for v in backlinks.values()),
        "tag_affinity_pairs": len(tag_affinity),
        "cluster_bridges": len(cluster_bridges),
        "similarity_entries": len(similarity),
    }

    return {
        "meta": meta,
        "link_graph": link_graph,
        "backlinks": backlinks,
        "tag_affinity": tag_affinity,
        "cluster_bridges": cluster_bridges,
        "similarity": similarity,
        "file_tags": file_tags,
    }
