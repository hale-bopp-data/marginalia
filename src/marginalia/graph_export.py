"""
graph_export.py — Export wiki knowledge graph for RAG expansion.

Combines:
  1. Link graph (scanner) — explicit markdown/wikilinks between documents
  2. Tag affinity (discovery) — implicit relationships via shared tags
  3. Cluster bridges (discovery) — multi-domain hub documents
  4. TF-IDF similarity (linker) — semantic neighbors by content
  5. Canonical sources (canonical) — entity -> authoritative file registry
  6. Structural (Cartografo KG import) — agent, repo, pipeline dependencies

Output: wiki-graph.json or unified-graph.json consumable by Alfred OODA.

PBI #971 — Graph RAG: Marginalia + Cartografo + Hybrid Search
Bug #1418 — Layer 5 (canonical_sources) added to prevent RAG source fabrication
PBI #2983 — Layer 6 (structural) + unified-graph merge Cartografo KG
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .scanner import build_file_index, build_graph, find_md_files, parse_frontmatter, extract_tags
from .discovery import discover_tag_affinity, discover_cluster_bridges
from .linker import build_suggestions
from .canonical import build_canonical_sources


def _load_kg_file(kg_path):
    """Load Cartografo knowledge-graph.json, returning (nodes, edges, meta, warnings)."""
    warnings = []
    kg_file = Path(kg_path)
    if not kg_file.exists():
        return [], [], None, [f"kg_not_found: {kg_path}"]
    try:
        kg = json.loads(kg_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [], [], None, [f"kg_parse_error: {e}"]
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    meta = kg.get("metadata") or kg.get("meta") or {}
    return nodes, edges, meta, warnings


def export_wiki_graph(vault_path, *, min_shared_tags=2, top_k_similar=5,
                      min_similarity=0.35, max_affinity=50, max_bridges=20,
                      ew_aware=False, external_linkers=None, vault_root_prefix=None):
    """Build a consolidated wiki graph combining all Marginalia relationship layers.

    Returns a dict ready to be serialized as wiki-graph.json.

    PBI #1966 — When ew_aware=True, link extraction also covers backtick code paths
    and frontmatter YAML link keys (related/superseded_by/see_also/parent/children/documents).
    external_linkers: paths to files/dirs outside the vault that may link in.
    vault_root_prefix: workspace-root prefix to strip from absolute paths.
    """
    base = Path(vault_path)
    file_index = build_file_index(base)

    # Layer 1: Link graph (explicit markdown + wikilinks; EW-aware extras when enabled)
    graph_data = build_graph(
        vault_path, file_index=file_index,
        ew_aware=ew_aware, external_linkers=external_linkers,
        vault_root_prefix=vault_root_prefix,
    )
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

    # Build file→tags index + frontmatter cache (compact)
    file_tags = {}
    file_frontmatter = {}
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content) or {}
            rel = str(f.relative_to(base)).replace("\\", "/")
            file_frontmatter[rel] = fm
            tags = extract_tags(fm)
            if tags:
                file_tags[rel] = tags
        except Exception:
            pass

    # Layer 5: Canonical sources (entity -> authoritative file for grounding)
    canonical_sources = build_canonical_sources(
        vault_path,
        backlinks=backlinks,
        file_frontmatter=file_frontmatter,
    )

    # Metadata
    meta = {
        "version": "1.1.0",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_path": str(base),
        "files_scanned": len(file_frontmatter),
        "link_graph_edges": sum(len(v) for v in link_graph.values()),
        "backlink_edges": sum(len(v) for v in backlinks.values()),
        "tag_affinity_pairs": len(tag_affinity),
        "cluster_bridges": len(cluster_bridges),
        "similarity_entries": len(similarity),
        "canonical_entities": len(canonical_sources),
    }

    return {
        "meta": meta,
        "link_graph": link_graph,
        "backlinks": backlinks,
        "tag_affinity": tag_affinity,
        "cluster_bridges": cluster_bridges,
        "similarity": similarity,
        "file_tags": file_tags,
        "canonical_sources": canonical_sources,
    }


# ---------------------------------------------------------------------------
# Unified Graph — Marginalia + Cartografo KG merge (PBI #2983)
# ---------------------------------------------------------------------------

def _normalize_kg_node(n, source="cartografo"):
    """Normalize a Cartografo KG node to the unified schema."""
    return {
        "id": n.get("id", ""),
        "type": n.get("type", "unknown"),
        "source": source,
        "label": n.get("label", n.get("id", "")),
        "properties": n.get("properties", {}),
    }


def _normalize_kg_edge(e, source="cartografo"):
    """Normalize a Cartografo KG edge to the unified schema."""
    return {
        "source": e.get("source", ""),
        "target": e.get("target", ""),
        "type": e.get("type", "unknown"),
        "source_graph": source,
        "properties": e.get("properties", {}),
    }


def _build_wiki_nodes(file_frontmatter, file_tags):
    """Build unified nodes from wiki files (Marginalia layer)."""
    nodes = []
    for rel, fm in file_frontmatter.items():
        node = {
            "id": rel,
            "type": "document",
            "source": "marginalia",
            "label": fm.get("title", Path(rel).stem),
            "properties": {
                "status": fm.get("status", ""),
                "tags": file_tags.get(rel, []),
            },
        }
        domain_tags = [t for t in file_tags.get(rel, []) if t.startswith("domain/")]
        if domain_tags:
            node["properties"]["domain"] = domain_tags[0].split("/", 1)[1]
        nodes.append(node)
    return nodes


def _build_marginalia_edges(link_graph, tag_affinity, similarity, cluster_bridges):
    """Convert Marginalia graph layers into unified edge list."""
    edges = []

    # Layer 1: explicit links (markdown + wikilinks)
    for source, targets in link_graph.items():
        for target in targets:
            edges.append({
                "source": source, "target": target,
                "type": "links_to", "source_graph": "marginalia",
            })

    # Layer 2: tag affinity
    for s in tag_affinity:
        edges.append({
            "source": s["source"], "target": s["target"],
            "type": "tag_affinity", "source_graph": "marginalia",
            "shared_tags": s.get("shared_tags", []),
            "score": s.get("score", 0),
        })

    # Layer 3: cluster bridges (multi-domain links)
    for b in cluster_bridges:
        edges.append({
            "source": b["file"], "target": b["file"],
            "type": "cluster_bridge", "source_graph": "marginalia",
            "domains": b.get("domains", []),
            "domain_count": b.get("domain_count", 0),
        })

    # Layer 4: TF-IDF similarity
    for source, sims in similarity.items():
        for s in sims:
            edges.append({
                "source": source, "target": s["path"],
                "type": "similar_to", "source_graph": "marginalia",
                "score": s.get("score", 0),
            })

    return edges


def _build_canonical_edges(canonical_sources):
    """Build cross-graph edges from canonical source mappings."""
    edges = []
    for entity, info in canonical_sources.items():
        primary = info["primary"]
        edges.append({
            "source": primary,
            "target": entity,
            "type": "canonical_of",
            "source_graph": "cross",
            "score": info.get("primary_score", 0),
        })
        for sec in info.get("secondary", []):
            edges.append({
                "source": sec,
                "target": entity,
                "type": "secondary_of",
                "source_graph": "cross",
            })
    return edges


def _link_wiki_to_kg(wiki_file_nodes, kg_nodes, file_frontmatter):
    """Create cross-graph edges linking wiki documents to KG entities (agents, repos)."""
    edges = []
    wiki_ids = {n["id"] for n in wiki_file_nodes}
    wiki_labels = {n["label"].lower(): n["id"] for n in wiki_file_nodes if n.get("label")}

    for kg_node in kg_nodes:
        kg_id = kg_node["id"]
        kg_label = kg_node.get("label", "").lower()
        kg_type = kg_node.get("type", "")

        # Match by label: if a wiki doc is about this KG entity
        if kg_label and kg_label in wiki_labels:
            edges.append({
                "source": kg_id,
                "target": wiki_labels[kg_label],
                "type": "documented_by",
                "source_graph": "cross",
            })

        # Match agent_xxx ids to wiki pages about agents
        if kg_type == "agent" and kg_id.startswith("agent_"):
            agent_name = kg_id.replace("agent_", "")
            for label, wiki_id in wiki_labels.items():
                if agent_name in label or label in agent_name:
                    edges.append({
                        "source": kg_id,
                        "target": wiki_id,
                        "type": "documented_by",
                        "source_graph": "cross",
                    })
                    break

    return edges


def export_unified_graph(vault_path, *, kg_path=None,
                         min_shared_tags=2, top_k_similar=5,
                         min_similarity=0.35, max_affinity=50, max_bridges=20,
                         ew_aware=False, external_linkers=None, vault_root_prefix=None):
    """Build unified graph merging Marginalia wiki graph + Cartografo KG (PBI #2983).

    Returns a single unified-graph.json with:
      - nodes[]: unified wiki doc nodes + Cartografo KG nodes
      - edges[]: all relationship types from both sources + cross-graph links
      - Full Marginalia layers preserved for backward compatibility
      - kg_raw: raw Cartografo KG data (when available)
      - warnings[]: any issues encountered during merge

    Graceful degradation: if kg_path is missing or unreadable, produces
    Marginalia-only graph with kg_available=false and appropriate warnings.
    """
    base = Path(vault_path)
    warnings = []

    # --- Build Marginalia wiki graph (Layers 1-5) ---
    wiki_graph = export_wiki_graph(
        vault_path,
        min_shared_tags=min_shared_tags,
        top_k_similar=top_k_similar,
        min_similarity=min_similarity,
        max_affinity=max_affinity,
        max_bridges=max_bridges,
        ew_aware=ew_aware,
        external_linkers=external_linkers,
        vault_root_prefix=vault_root_prefix,
    )

    # Extract components from wiki graph
    link_graph = wiki_graph.get("link_graph", {})
    backlinks = wiki_graph.get("backlinks", {})
    tag_affinity = wiki_graph.get("tag_affinity", [])
    cluster_bridges = wiki_graph.get("cluster_bridges", [])
    similarity = wiki_graph.get("similarity", {})
    file_tags = wiki_graph.get("file_tags", {})
    canonical_sources = wiki_graph.get("canonical_sources", {})

    # Rebuild file_frontmatter from file_tags keys
    file_frontmatter = {}
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content) or {}
            rel = str(f.relative_to(base)).replace("\\", "/")
            file_frontmatter[rel] = fm
        except Exception:
            pass

    # --- Build unified nodes ---
    wiki_nodes = _build_wiki_nodes(file_frontmatter, file_tags)
    wiki_node_ids = {n["id"] for n in wiki_nodes}

    # --- Load Cartografo KG ---
    kg_nodes, kg_edges, kg_meta, kg_warnings = [], [], None, []
    kg_available = False
    if kg_path:
        kg_nodes, kg_edges, kg_meta, kg_warnings = _load_kg_file(kg_path)
        kg_available = bool(kg_meta and kg_nodes)
        warnings.extend(kg_warnings)
    else:
        warnings.append("kg_path_not_provided: no Cartografo KG to merge")

    # Normalize KG nodes/edges to unified schema
    unified_kg_nodes = [_normalize_kg_node(n) for n in kg_nodes]
    unified_kg_edges = [_normalize_kg_edge(e) for e in kg_edges]

    # Deduplicate wiki nodes vs kg nodes (by id)
    for kgn in unified_kg_nodes:
        if kgn["id"] not in wiki_node_ids:
            wiki_nodes.append(kgn)

    # --- Build unified edges ---
    unified_edges = _build_marginalia_edges(link_graph, tag_affinity, similarity, cluster_bridges)
    unified_edges.extend(unified_kg_edges)

    # Cross-graph links
    unified_edges.extend(_build_canonical_edges(canonical_sources))
    unified_edges.extend(_link_wiki_to_kg(wiki_nodes, unified_kg_nodes, file_frontmatter))

    # --- Deduplicate edges ---
    seen = set()
    deduped = []
    for e in unified_edges:
        key = (e["source"], e["target"], e["type"], e.get("source_graph", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    unified_edges = deduped

    # --- Metadata ---
    wm = wiki_graph["meta"]
    meta = {
        "version": "2.0.0",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_path": str(base),
        "kg_path": str(kg_path) if kg_path else None,
        "kg_available": kg_available,
        "kg_built_at": kg_meta.get("last_updated", "") if kg_meta else "",
        "kg_builder": kg_meta.get("builder", "") if kg_meta else "",
        "files_scanned": wm["files_scanned"],
        "link_graph_edges": wm["link_graph_edges"],
        "backlink_edges": wm["backlink_edges"],
        "tag_affinity_pairs": wm["tag_affinity_pairs"],
        "cluster_bridges": wm["cluster_bridges"],
        "similarity_entries": wm["similarity_entries"],
        "canonical_entities": wm["canonical_entities"],
        "kg_nodes": len(unified_kg_nodes),
        "kg_edges": len(unified_kg_edges),
        "total_nodes": len(wiki_nodes),
        "total_edges": len(unified_edges),
        "warnings": warnings if warnings else [],
    }

    return {
        "meta": meta,
        "nodes": wiki_nodes,
        "edges": unified_edges,
        # Backward-compatible Marginalia layers
        "link_graph": link_graph,
        "backlinks": backlinks,
        "tag_affinity": tag_affinity,
        "cluster_bridges": cluster_bridges,
        "similarity": similarity,
        "file_tags": file_tags,
        "canonical_sources": canonical_sources,
        # Raw Cartografo KG (for consumers that need it)
        "kg_raw": {
            "metadata": kg_meta,
            "nodes": unified_kg_nodes,
            "edges": unified_kg_edges,
        } if kg_available else None,
    }
