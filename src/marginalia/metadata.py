"""ew-metadata.py — Metadata DB adapter (Cartografo + Marginalia fusion).

G16 Presa Elettrica: interfaccia standard. Chiamato da Caronte /api/metadata.
Fonde dati strutturali (Cartografo) e semantici (Marginalia) per risposta unificata.
I dati ADO (WI, PR, sprint) sono aggiunti dal chiamante (Caronte).

Fast path (default): scansione tag frontmatter (<2s su 1100+ file).
Slow path (--wiki-graph-json): full graph export via export_wiki_graph().

Usage:
    python ew-metadata.py --wi-id 2247
    python ew-metadata.py --agent alfred
    python ew-metadata.py --repo easyway-wiki
    python ew-metadata.py --search "deploy sicurezza"
    python ew-metadata.py --wiki-graph-json         # solo graph export (cached)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from marginalia.scanner import find_md_files, parse_frontmatter, extract_tags, build_file_index
from marginalia.canonical import _file_prefix as _canonical_prefix, build_canonical_sources

# ---------------------------------------------------------------------------
# Paths canonici (overridable via env)
# ---------------------------------------------------------------------------
_VAULT_DEFAULT = os.environ.get(
    "EW_WIKI_PATH",
    "C:/EW/easyway/wiki" if os.name == "nt" else "/home/ubuntu/easyway-wiki",
)
CARTOGRAFO_PATH = os.environ.get(
    "EW_CARTOGRAFO_PATH",
    "C:/EW/easyway/opt/knowledge-graph.json" if os.name == "nt"
    else "/opt/easyway/knowledge-graph.json",
)
WIKI_GRAPH_CACHE = os.environ.get(
    "EW_WIKI_GRAPH_CACHE",
    "C:/EW/easyway/opt/wiki-graph.json" if os.name == "nt"
    else "/opt/easyway/wiki-graph.json",
)
_FAST_SCAN_CACHE = None  # in-memory cache per sessione
_FAST_SCAN_MTIME = 0


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Fast scan: tag index + frontmatter (no full graph export)
# ---------------------------------------------------------------------------
def _fast_scan(vault_path, force=False):
    """Scan vault frontmatter tags only. Cached in-memory per session.

    Returns: {files: [{path, title, tags, status}], tag_index: {tag: [file_path]}}
    """
    global _FAST_SCAN_CACHE, _FAST_SCAN_MTIME
    vault = Path(vault_path)

    if not force and _FAST_SCAN_CACHE is not None:
        try:
            mtime = vault.stat().st_mtime
            if mtime == _FAST_SCAN_MTIME:
                return _FAST_SCAN_CACHE
        except OSError:
            pass

    files = []
    tag_index = {}

    for f in find_md_files(vault):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content) or {}
            rel = str(f.relative_to(vault)).replace("\\", "/")
            tags = extract_tags(fm)
            entry = {
                "path": rel,
                "title": fm.get("title", f.stem),
                "tags": tags,
                "status": fm.get("status", ""),
            }
            files.append(entry)
            for tag in tags:
                tag_index.setdefault(tag, []).append(rel)
        except Exception:
            continue

    try:
        _FAST_SCAN_MTIME = vault.stat().st_mtime
    except OSError:
        _FAST_SCAN_MTIME = 0
    _FAST_SCAN_CACHE = {"files": files, "tag_index": tag_index}
    return _FAST_SCAN_CACHE


# ---------------------------------------------------------------------------
# Wiki chunk matcher (fast: tag-based + canonical boost)
# ---------------------------------------------------------------------------
def find_wiki_chunks(query_keywords, vault_path=None, max_results=5):
    """Find relevant wiki chunks by matching keywords against tags and titles.

    Uses fast frontmatter scan — NO TF-IDF, NO full graph export.
    """
    vault_path = vault_path or _VAULT_DEFAULT
    data = _fast_scan(vault_path)
    files = data["files"]
    tag_index = data["tag_index"]

    query_lower = [kw.lower() for kw in query_keywords]
    scored = []

    for entry in files:
        score = 0
        tag_text = " ".join(entry["tags"]).lower()
        title_lower = entry["title"].lower()
        path_lower = entry["path"].lower()

        for kw in query_lower:
            if kw in tag_text:
                score += 3  # tag match = strong signal
            if kw in title_lower:
                score += 5  # title match = strongest
            if kw in path_lower:
                score += 2  # path match = moderate

        if score > 0:
            scored.append((entry, score))

    scored.sort(key=lambda x: -x[1])

    chunks = []
    seen_paths = set()
    for entry, score in scored[:max_results]:
        if entry["path"] in seen_paths:
            continue
        seen_paths.add(entry["path"])

        chunk = {
            "path": entry["path"],
            "title": entry["title"],
            "similarity_score": round(min(score / 8.0, 1.0), 2),
            "tags": entry["tags"],
        }

        # Canonical entity check
        entity = _canonical_prefix(entry["path"])
        if entity:
            chunk["canonical_for"] = [entity]

        chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Cartografo loader
# ---------------------------------------------------------------------------
def load_cartografo(path=None):
    """Load Cartografo knowledge-graph.json if available."""
    path = path or CARTOGRAFO_PATH
    p = Path(path)
    if not p.exists():
        return None, {"source": "cartografo", "message": f"File not found: {path}", "severity": "info"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return None, {"source": "cartografo", "message": f"Parse error: {e}", "severity": "error"}

    # Check staleness (>48h)
    updated = data.get("meta", {}).get("updated") or data.get("updated", "")
    if updated:
        try:
            from datetime import timedelta
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - updated_dt
            if age > timedelta(hours=48):
                return data, {"source": "cartografo", "message": f"Stale >48h (updated {updated})", "severity": "warning"}
        except (ValueError, TypeError):
            pass

    return data, None


# ---------------------------------------------------------------------------
# Full wiki-graph export (slow — cached, for offline/cron use)
# ---------------------------------------------------------------------------
def export_wiki_graph_full(vault_path=None, force=False):
    """Full wiki-graph via Marginalia export_wiki_graph(). Caches to disk."""
    vault_path = vault_path or _VAULT_DEFAULT
    cache_path = Path(WIKI_GRAPH_CACHE)

    if not force and cache_path.exists():
        try:
            age = datetime.now().timestamp() - cache_path.stat().st_mtime
            if age < 3600:  # cache valida 1h
                return json.loads(cache_path.read_text(encoding="utf-8")), None
        except (json.JSONDecodeError, OSError):
            pass

    try:
        from marginalia.graph_export import export_wiki_graph  # noqa: PLC0415
        graph = export_wiki_graph(
            vault_path,
            min_shared_tags=2,
            top_k_similar=5,
            min_similarity=0.35,
            max_affinity=50,
            max_bridges=20,
            ew_aware=True,
        )
    except Exception as e:
        return None, {"source": "marginalia", "message": f"Export failed: {e}", "severity": "error"}

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    return graph, None


# ---------------------------------------------------------------------------
# Dependency fusion (structural + semantic)
# ---------------------------------------------------------------------------
def fuse_dependencies(cartografo, query_keywords=None):
    """Merge structural (Cartografo) dependencies. Semantic layer (Marginalia)
    is represented by wiki_chunks already."""
    deps = {"structural": []}

    if cartografo:
        edges = cartografo.get("edges", []) or cartografo.get("dependencies", [])
        for edge in edges[:20]:
            deps["structural"].append({
                "source": edge.get("source", ""),
                "target": edge.get("target", ""),
                "relation": edge.get("relation", edge.get("type", "related")),
            })

    return deps


# ---------------------------------------------------------------------------
# Main query dispatcher
# ---------------------------------------------------------------------------
def query_metadata(query_type, query_value, vault_path=None, cartografo_path=None):
    """Execute a metadata query and return unified JSON per schema v1.0."""
    warnings = []
    vault_path = vault_path or _VAULT_DEFAULT

    response = {
        "meta": {
            "schema_version": "1.0.0",
            "generated_at": _now_iso(),
            "ttl_seconds": 60,
            "sources": ["cartografo", "marginalia"],
        },
        "wi": None,
        "repo": None,
        "pr": None,
        "agent": None,
        "wiki_chunks": [],
        "dependencies": {"structural": []},
        "errors": [],
        "warnings": [],
    }

    # Load Cartografo
    cartografo, cw = load_cartografo(cartografo_path)
    if cw:
        warnings.append(cw)

    # Dispatch
    if query_type == "wi_id":
        response["wi"] = {"id": query_value}
        keywords = [str(query_value)]
        response["wiki_chunks"] = find_wiki_chunks(keywords, vault_path)
        response["dependencies"] = fuse_dependencies(cartografo, keywords)

    elif query_type == "agent":
        response["agent"] = {"id": query_value}
        keywords = [str(query_value), "agent"]
        response["wiki_chunks"] = find_wiki_chunks(keywords, vault_path)
        response["dependencies"] = fuse_dependencies(cartografo, keywords)

    elif query_type == "repo":
        response["repo"] = {"name": query_value}
        keywords = [str(query_value)]
        response["wiki_chunks"] = find_wiki_chunks(keywords, vault_path)
        response["dependencies"] = fuse_dependencies(cartografo, keywords)

    elif query_type == "search":
        keywords = query_value.split() if isinstance(query_value, str) else query_value
        response["wiki_chunks"] = find_wiki_chunks(keywords, vault_path)
        response["dependencies"] = fuse_dependencies(cartografo, keywords)

    else:
        response["errors"].append({"message": f"Unknown query_type: {query_type}"})

    response["warnings"] = warnings
    return response


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="EW Metadata DB Adapter (Cartografo + Marginalia)")
    parser.add_argument("--wi-id", type=int, help="Work item ID")
    parser.add_argument("--agent", type=str, help="Agent ID")
    parser.add_argument("--repo", type=str, help="Repository name")
    parser.add_argument("--search", type=str, help="Free-text keyword search")
    parser.add_argument("--wiki-graph-json", action="store_true", help="Output full wiki graph (slow, cached)")
    parser.add_argument("--vault-path", type=str, default=_VAULT_DEFAULT, help="Wiki vault path")
    parser.add_argument("--cartografo-path", type=str, default=CARTOGRAFO_PATH)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    if args.wiki_graph_json:
        graph, warn = export_wiki_graph_full(args.vault_path, force=True)
        if warn:
            print(json.dumps({"error": warn["message"]}, indent=2), file=sys.stderr)
            sys.exit(1)
        indent = 2 if args.pretty else None
        print(json.dumps(graph, indent=indent, ensure_ascii=False))
        return

    if args.wi_id:
        result = query_metadata("wi_id", args.wi_id, args.vault_path, args.cartografo_path)
    elif args.agent:
        result = query_metadata("agent", args.agent, args.vault_path, args.cartografo_path)
    elif args.repo:
        result = query_metadata("repo", args.repo, args.vault_path, args.cartografo_path)
    elif args.search:
        result = query_metadata("search", args.search, args.vault_path, args.cartografo_path)
    else:
        result = {"error": "No query specified. Use --wi-id, --agent, --repo, --search, or --wiki-graph-json."}

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()
