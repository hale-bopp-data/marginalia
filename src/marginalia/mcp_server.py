"""
MCP Server for Marginalia — exposes tag-graph, scan status, and discovery tools
to AI agents via the Model Context Protocol.

Inspired by graphify-8 serve.py pattern — low-level mcp SDK.
PBI #2976.

Usage:
    marginalia-mcp                          # stdio transport (default)
    marginalia-mcp --vault /path/to/vault   # with default vault
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .scanner import build_graph, find_md_files
from .discovery import discover_orphan_homes, discover_tag_affinity
from .graph_export import build_dependency_matrix


# ---------------------------------------------------------------------------
# Business logic — pure functions, no MCP dependency
# ---------------------------------------------------------------------------

def _scan_status(vault_path: str) -> dict:
    """Build a lightweight scan status summary."""
    base = Path(vault_path)
    if not base.exists():
        return {"error": f"Vault not found: {vault_path}", "status": "unavailable"}

    try:
        md_files = find_md_files(base)
        graph = build_graph(vault_path)
    except Exception as e:
        return {"error": str(e), "status": "error"}

    return {
        "status": "ok",
        "vault": str(base),
        "files_scanned": len(md_files),
        "tags": {
            "total": graph["tag_count"],
            "namespaced": graph["namespaced_tags"],
            "flat": graph["flat_tags"],
        },
        "links": {
            "total": graph["link_count"],
            "files_with_links": graph["topology"]["files_with_links"],
            "files_linked_to": graph["topology"]["files_linked_to"],
        },
        "orphans": graph["orphan_count"],
        "clusters": graph["clusters"],
        "top_hubs": graph["topology"]["hubs"][:5],
        "top_authorities": graph["topology"]["authorities"][:5],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _query_tags(vault_path: str, tag: str = None, namespace: str = None) -> dict:
    """Query the tag index. Returns files grouped by tag."""
    base = Path(vault_path)
    if not base.exists():
        return {"error": f"Vault not found: {vault_path}"}

    try:
        graph = build_graph(vault_path)
    except Exception as e:
        return {"error": str(e)}

    tag_index = graph["tag_index"]

    if tag:
        files = tag_index.get(tag, [])
        return {"tag": tag, "file_count": len(files), "files": files}

    if namespace:
        matching = {t: f for t, f in tag_index.items() if t.startswith(namespace + "/")}
        return {
            "namespace": namespace,
            "tag_count": len(matching),
            "tags": {t: {"file_count": len(f)} for t, f in sorted(matching.items())},
        }

    return {
        "tag_count": len(tag_index),
        "tags": {t: len(f) for t, f in sorted(tag_index.items())},
    }


def _get_related(vault_path: str, file: str, max_results: int = 10) -> dict:
    """Find files related to a given file via tags, links, and tag affinity."""
    base = Path(vault_path)
    if not base.exists():
        return {"error": f"Vault not found: {vault_path}"}

    try:
        graph = build_graph(vault_path)
        affinity = discover_tag_affinity(vault_path, max_results=max_results)
    except Exception as e:
        return {"error": str(e)}

    result = {"file": file, "related": []}
    seen = set()

    def _add(rel_file: str, relation: str, direction: str = None, extra: dict = None):
        if rel_file in seen:
            return
        seen.add(rel_file)
        entry = {"file": rel_file, "relation": relation}
        if direction:
            entry["direction"] = direction
        if extra:
            entry.update(extra)
        result["related"].append(entry)

    # 1. Outgoing links
    for target in graph["link_graph"].get(file, []):
        _add(target, "links_to", "outgoing")

    # 2. Incoming links (backlinks)
    for source, targets in graph["link_graph"].items():
        if file in targets:
            _add(source, "linked_from", "incoming")

    # 3. Tag affinity
    for conn in affinity:
        if file in (conn.get("file_a"), conn.get("file_b")):
            other = conn["file_b"] if conn["file_a"] == file else conn["file_a"]
            _add(other, "tag_affinity", extra={"shared_tags": conn.get("shared_tags", 0)})

    result["total"] = len(result["related"])
    return result


def _list_orphans(vault_path: str, max_results: int = 30) -> dict:
    """List orphan files with suggested parent links."""
    base = Path(vault_path)
    if not base.exists():
        return {"error": f"Vault not found: {vault_path}"}

    try:
        graph = build_graph(vault_path)
        orphan_homes = discover_orphan_homes(vault_path, max_results=max_results)
    except Exception as e:
        return {"error": str(e)}

    return {
        "orphan_count": graph["orphan_count"],
        "orphans": graph["orphans"][:max_results],
        "suggestions": orphan_homes,
    }


def _unified_graph_query(graph_path: str, query: str, depth: int = 2,
                          include_cross_links: bool = True) -> dict:
    """Query unified-graph.json: search nodes/edges, traverse dependencies (PBI #2984)."""
    ug_file = Path(graph_path)
    if not ug_file.exists():
        return {"error": f"Unified graph not found: {graph_path}", "status": "unavailable"}

    try:
        ug = json.loads(ug_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to load unified graph: {e}", "status": "error"}

    nodes = ug.get("nodes", [])
    edges = ug.get("edges", [])
    meta = ug.get("meta", {})

    q = query.lower()

    # Search nodes
    matched_nodes = []
    for n in nodes:
        nid = (n.get("id") or "").lower()
        nlabel = (n.get("label") or "").lower()
        ntype = (n.get("type") or "").lower()
        if q in nid or q in nlabel or q in ntype:
            matched_nodes.append(n)

    if not matched_nodes:
        return {
            "query": query,
            "matched_nodes": 0,
            "graph_meta": {
                "total_nodes": meta.get("total_nodes", 0),
                "total_edges": meta.get("total_edges", 0),
                "kg_available": meta.get("kg_available", False),
                "built_at": meta.get("built_at", ""),
            },
            "hint": f"No nodes match '{query}'. Try a broader term or check graph_path.",
        }

    # For each matched node, collect edges up to depth
    matched_ids = {n["id"] for n in matched_nodes}
    result_nodes = {}
    result_edges = []

    # BFS traversal
    frontier = set(matched_ids)
    visited = set()
    for _ in range(depth + 1):
        next_frontier = set()
        for e in edges:
            src = e.get("source", "")
            tgt = e.get("target", "")
            eg_type = e.get("type", "")
            eg_source = e.get("source_graph", "")

            # Skip cross-links if not requested
            if eg_source == "cross" and not include_cross_links:
                continue

            if src in frontier or tgt in frontier:
                result_edges.append(e)
                if src not in visited:
                    next_frontier.add(src)
                if tgt not in visited:
                    next_frontier.add(tgt)

        visited.update(frontier)
        frontier = next_frontier - visited
        if not frontier:
            break

    # Collect all nodes referenced by result edges
    all_ids = set()
    for e in result_edges:
        all_ids.add(e.get("source", ""))
        all_ids.add(e.get("target", ""))

    for n in nodes:
        if n["id"] in all_ids:
            result_nodes[n["id"]] = {
                "id": n["id"],
                "label": n.get("label", n["id"]),
                "type": n.get("type", "unknown"),
                "source": n.get("source", "unknown"),
                "properties": n.get("properties", {}),
            }

    # Blast radius: incoming edges (who depends on matched nodes)
    blast_radius = []
    for e in edges:
        if e.get("target") in matched_ids and e.get("source") not in matched_ids:
            src_id = e["source"]
            src_node = next((n for n in nodes if n["id"] == src_id), None)
            blast_radius.append({
                "source": src_id,
                "label": src_node.get("label", src_id) if src_node else src_id,
                "type": e.get("type", "unknown"),
                "source_graph": e.get("source_graph", "unknown"),
            })

    # Edge type breakdown
    edge_types = {}
    for e in result_edges:
        t = e.get("type", "unknown")
        edge_types[t] = edge_types.get(t, 0) + 1

    return {
        "query": query,
        "matched_nodes": len(matched_nodes),
        "matched": [{"id": n["id"], "label": n.get("label", n["id"]), "type": n.get("type", "unknown")}
                    for n in matched_nodes[:20]],
        "nodes": list(result_nodes.values()),
        "edges": result_edges,
        "blast_radius": blast_radius,
        "edge_types": edge_types,
        "graph_meta": {
            "total_nodes": meta.get("total_nodes", 0),
            "total_edges": meta.get("total_edges", 0),
            "kg_available": meta.get("kg_available", False),
            "built_at": meta.get("built_at", ""),
        },
    }


# ---------------------------------------------------------------------------
# MCP Server — graphify-8 pattern
# ---------------------------------------------------------------------------

def _build_server(vault_path: str):
    """Build the configured low-level MCP Server (shared by every transport)."""
    try:
        from mcp.server import Server
        from mcp import types
    except ImportError:
        raise ImportError(
            "MCP SDK not installed. Install with: pip install mcp"
        )

    default_vault = vault_path or "."

    server = Server("marginalia-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="scan_status",
                description=(
                    "Quick scan of a Markdown vault: file count, tag stats, links, "
                    "orphans, clusters, top hubs and authorities. Use this to assess "
                    "vault quality before making changes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault": {
                            "type": "string",
                            "description": "Path to the Markdown vault. Uses configured default if omitted.",
                        },
                    },
                },
            ),
            types.Tool(
                name="query_tags",
                description=(
                    "Query the Marginalia tag index. Find which files carry a specific "
                    "tag, or browse all tags in a namespace (e.g. 'domain', 'artifact'). "
                    "Without arguments, returns the full tag dictionary."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault": {"type": "string", "description": "Path to the Markdown vault."},
                        "tag": {"type": "string", "description": "Exact tag to query (e.g. 'domain/security')."},
                        "namespace": {"type": "string", "description": "Tag namespace prefix (e.g. 'domain')."},
                    },
                },
            ),
            types.Tool(
                name="get_related",
                description=(
                    "Find files related to a given file through three layers: "
                    "outgoing links, incoming backlinks, and tag affinity "
                    "(files sharing tags but not explicitly linked)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault": {"type": "string", "description": "Path to the Markdown vault."},
                        "file": {"type": "string", "description": "Relative path of the file to find relations for."},
                        "max_results": {"type": "integer", "default": 10, "description": "Maximum results (default: 10)."},
                    },
                    "required": ["file"],
                },
            ),
            types.Tool(
                name="list_orphans",
                description=(
                    "List orphan files (no inbound links from any other file) with "
                    "suggested parent links discovered via sibling pattern and name "
                    "similarity heuristics."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault": {"type": "string", "description": "Path to the Markdown vault."},
                        "max_results": {"type": "integer", "default": 30, "description": "Maximum orphans to return (default: 30)."},
                    },
                },
            ),
            types.Tool(
                name="unified_graph_query",
                description=(
                    "Query the unified graph (Marginalia + Cartografo KG merge, PBI #2983). "
                    "Search nodes by id/label/type, traverse edges up to configurable depth, "
                    "compute blast radius (who depends on matched nodes). Returns matched nodes, "
                    "related edges, edge type breakdown, and cross-graph links. "
                    "Use for impact analysis, dependency mapping, and discovering structural + "
                    "semantic connections in the EasyWay knowledge graph."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "graph_path": {
                            "type": "string",
                            "description": "Path to unified-graph.json (default: kb/unified-graph.json).",
                        },
                        "query": {
                            "type": "string",
                            "description": "Search term (matches node id, label, or type).",
                        },
                        "depth": {
                            "type": "integer",
                            "default": 2,
                            "description": "Graph traversal depth from matched nodes (default: 2).",
                        },
                        "include_cross_links": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include cross-graph edges (documented_by, canonical_of) in results (default: true).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="dependency_matrix",
                description=(
                    "Build a cross-tabulation dependency matrix from unified-graph.json (PBI #2986). "
                    "Shows who-depends-on-whom as rows/columns with pairwise dependency counts. "
                    "Filterable by node type (agent, document, repo, pipeline, config). "
                    "Use for dispatch routing, blast radius overview, and pettegola mode."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "graph_path": {
                            "type": "string",
                            "description": "Path to unified-graph.json.",
                        },
                        "node_types": {
                            "type": "string",
                            "description": "Filter by node types, comma-separated (e.g. 'agent,document'). Omit for all types.",
                        },
                        "min_deps": {
                            "type": "integer",
                            "default": 1,
                            "description": "Minimum dependency count to include a row (default: 1).",
                        },
                        "top_n": {
                            "type": "integer",
                            "default": 50,
                            "description": "Maximum rows/columns in the matrix (default: 50).",
                        },
                    },
                    "required": ["graph_path"],
                },
            ),
        ]

    # Handler dispatch table
    _handlers = {
        "scan_status": lambda args: json.dumps(
            _scan_status(args.get("vault", default_vault)), indent=2, ensure_ascii=False
        ),
        "query_tags": lambda args: json.dumps(
            _query_tags(
                args.get("vault", default_vault),
                tag=args.get("tag"),
                namespace=args.get("namespace"),
            ),
            indent=2,
            ensure_ascii=False,
        ),
        "get_related": lambda args: json.dumps(
            _get_related(
                args.get("vault", default_vault),
                file=args["file"],
                max_results=args.get("max_results", 10),
            ),
            indent=2,
            ensure_ascii=False,
        ),
        "list_orphans": lambda args: json.dumps(
            _list_orphans(
                args.get("vault", default_vault),
                max_results=args.get("max_results", 30),
            ),
            indent=2,
            ensure_ascii=False,
        ),
        "unified_graph_query": lambda args: json.dumps(
            _unified_graph_query(
                graph_path=args.get("graph_path", str(Path(default_vault).parent / "kb" / "unified-graph.json")),
                query=args["query"],
                depth=args.get("depth", 2),
                include_cross_links=args.get("include_cross_links", True),
            ),
            indent=2,
            ensure_ascii=False,
        ),
        "dependency_matrix": lambda args: json.dumps(
            build_dependency_matrix(
                graph_path=args["graph_path"],
                node_types=args.get("node_types", "").split(",") if args.get("node_types") else None,
                min_deps=args.get("min_deps", 1),
                top_n=args.get("top_n", 50),
            ),
            indent=2,
            ensure_ascii=False,
        ),
    }

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        handler = _handlers.get(name)
        if not handler:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            return [types.TextContent(type="text", text=handler(arguments))]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error executing {name}: {exc}")]

    return server


# ---------------------------------------------------------------------------
# Transport entry points
# ---------------------------------------------------------------------------

def serve(vault_path: str = None):
    """Start the MCP server over stdio (the default transport)."""
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise ImportError("MCP SDK not installed. Install with: pip install mcp")
    import asyncio

    server = _build_server(vault_path)

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(main())


def main():
    """Entry point for `marginalia-mcp` command."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Marginalia MCP Server — expose tag-graph to AI agents"
    )
    parser.add_argument(
        "--vault", "-v",
        default=None,
        help="Default vault path for all tools",
    )
    parser.add_argument(
        "--transport", "-t",
        choices=["stdio"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )

    args = parser.parse_args()

    if args.transport == "stdio":
        serve(vault_path=args.vault)
    else:
        print(f"Unknown transport: {args.transport}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
