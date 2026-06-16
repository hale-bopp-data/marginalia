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
