"""Operator-facing catalog and quickstart blueprint helpers."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .discovery import discover_all
from .linker import build_suggestions
from .obsidian import check_all
from .scanner import (
    build_file_index,
    build_graph,
    build_tag_dictionary,
    find_md_files,
    scan_file,
)


CATALOG = [
    {
        "id": "baseline-scan",
        "category": "baseline",
        "goal": "See what is broken right now",
        "command": "marginalia scan <vault>",
        "outputs": ["issue report", "graph summary", "exit code"],
        "when_to_use": "Start here on every new vault or before a cleanup cycle.",
    },
    {
        "id": "operator-quickstart",
        "category": "baseline",
        "goal": "Get a guided operator flow",
        "command": "marginalia quickstart <vault> --write",
        "outputs": ["operator blueprint", "recommended commands", "quickstart report"],
        "when_to_use": "Use when you want the tool to tell you the next best slice.",
    },
    {
        "id": "fix-pipeline",
        "category": "normalize",
        "goal": "Apply structural fixes safely",
        "command": "marginalia fix <vault> --apply",
        "outputs": ["fix summary", "per-giro changes"],
        "when_to_use": "After scan shows frontmatter, link, or heading hygiene issues.",
    },
    {
        "id": "tag-catalog",
        "category": "catalog",
        "goal": "Understand the tag landscape",
        "command": "marginalia tags <vault>",
        "outputs": ["tag dictionary", "synonym candidates", "coverage summary"],
        "when_to_use": "When the operator needs a catalog of metadata quality and drift.",
    },
    {
        "id": "tag-rationalization",
        "category": "catalog",
        "goal": "Prepare taxonomy normalization",
        "command": "marginalia tags <vault> --analyze --out tag-inventory.json",
        "outputs": ["tag inventory", "reasoned suggestions"],
        "when_to_use": "When flat tags or missing domain routing block consistent retrieval.",
    },
    {
        "id": "link-materialization",
        "category": "materialize",
        "goal": "Write suggested connections into the vault",
        "command": "marginalia link <vault> --apply --no-what-if",
        "outputs": ["suggestion JSON", "backup dir", "See also sections"],
        "when_to_use": "When you want to materialize latent connections after cleanup.",
    },
    {
        "id": "hidden-connections",
        "category": "discover",
        "goal": "Find meaningful but missing links",
        "command": "marginalia discover <vault>",
        "outputs": ["tag affinity", "orphan homes", "cluster bridges"],
        "when_to_use": "When the vault is structurally clean but semantically thin.",
    },
    {
        "id": "obsidian-health",
        "category": "guardrails",
        "goal": "Check Obsidian-specific hygiene",
        "command": "marginalia check <vault>",
        "outputs": ["gitignore issues", "wikilink resolution", "structure warnings"],
        "when_to_use": "When you need operator confidence before sharing or automating a vault.",
    },
    {
        "id": "rag-eval",
        "category": "measure",
        "goal": "Measure retrieval quality before and after changes",
        "command": "marginalia eval snapshot <vault> --queries queries.yaml --out before.json",
        "outputs": ["snapshot metrics", "top1 score", "coverage"],
        "when_to_use": "When you need evidence that cleanup improved discoverability.",
    },
]


def get_catalog():
    """Return the operator capability catalog."""
    return list(CATALOG)


def _recommendations(issue_counts, graph, tag_dict, obsidian_issues, discoveries):
    """Build a small ordered operator flow from vault signals."""
    recs = []

    if issue_counts:
        top_issue = issue_counts[0]["type"]
        recs.append({
            "id": "baseline-scan",
            "title": "Stabilize the baseline",
            "priority": "high",
            "why": f"The vault has {sum(i['count'] for i in issue_counts)} scan issues; start from the biggest bucket: {top_issue}.",
            "command": "marginalia scan <vault> --tag",
        })

    if tag_dict["flat"] or any(i["type"] == "missing_domain_tag" for i in issue_counts):
        recs.append({
            "id": "tag-catalog",
            "title": "Build the metadata catalog",
            "priority": "high",
            "why": f"{tag_dict['flat']} flat tags and domain coverage gaps are reducing routing quality.",
            "command": "marginalia tags <vault>",
        })

    if any(i["type"] in {"missing_frontmatter", "incomplete_frontmatter", "stale_link", "broken_link", "broken_wikilink", "empty_section"} for i in issue_counts):
        recs.append({
            "id": "fix-pipeline",
            "title": "Run the structural fix pipeline",
            "priority": "high",
            "why": "The current issue mix is mostly auto-fixable or normalization-friendly.",
            "command": "marginalia fix <vault> --apply",
        })

    hidden_connections = discoveries["tag_affinity"]["count"] + discoveries["orphan_homes"]["count"]
    if graph["orphan_count"] or hidden_connections:
        recs.append({
            "id": "link-materialization",
            "title": "Materialize useful connections",
            "priority": "medium",
            "why": f"{graph['orphan_count']} orphans and {hidden_connections} latent connections suggest the vault needs linking, not only linting.",
            "command": "marginalia link <vault> --apply --no-what-if",
        })

    if obsidian_issues:
        recs.append({
            "id": "obsidian-health",
            "title": "Harden Obsidian hygiene",
            "priority": "medium",
            "why": f"{len(obsidian_issues)} Obsidian-specific issues can surprise operators even after content cleanup.",
            "command": "marginalia check <vault>",
        })

    recs.append({
        "id": "rag-eval",
        "title": "Measure the delta",
        "priority": "medium",
        "why": "Close the loop with evidence once the cleanup or linking pass is done.",
        "command": "marginalia eval snapshot <vault> --queries queries.yaml --out after.json",
    })
    return recs[:5]


def build_quickstart_blueprint(vault_path, required_fields=None, max_depth=5):
    """Inspect a vault and return a guided operator blueprint."""
    vault = Path(vault_path).resolve()
    file_index = build_file_index(vault)
    md_files = find_md_files(vault)
    required_fields = required_fields or ["title", "tags"]

    issues = []
    for md_file in md_files:
        issues.extend(scan_file(md_file, vault, file_index=file_index, required_fields=required_fields))

    issue_counter = Counter(issue["type"] for issue in issues)
    issue_counts = [
        {"type": issue_type, "count": count}
        for issue_type, count in issue_counter.most_common(8)
    ]

    graph = build_graph(vault, file_index=file_index)
    tag_dict = build_tag_dictionary(vault, file_index=file_index)
    discoveries = discover_all(vault, max_results=12)
    obsidian_issues = check_all(vault, max_depth=max_depth)
    link_suggestions = build_suggestions(vault, top_k=3)

    hidden_connections = discoveries["tag_affinity"]["count"]
    suggested_links = sum(len(item["suggestions"]) for item in link_suggestions[:10])
    stage = "connect" if not issues and (graph["orphan_count"] or hidden_connections) else "stabilize"
    if not issues and not graph["orphan_count"] and not hidden_connections:
        stage = "measure"

    blueprint = {
        "action": "marginalia-quickstart",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vault": str(vault),
        "summary": {
            "files": len(md_files),
            "scan_issues": len(issues),
            "obsidian_issues": len(obsidian_issues),
            "orphans": graph["orphan_count"],
            "flat_tags": tag_dict["flat"],
            "tag_affinity_candidates": hidden_connections,
            "cluster_bridges": discoveries["cluster_bridges"]["count"],
            "suggested_links_preview": suggested_links,
            "operator_stage": stage,
        },
        "top_issues": issue_counts,
        "recommended_flow": _recommendations(issue_counts, graph, tag_dict, obsidian_issues, discoveries),
        "artifacts": {
            "catalog_entrypoint": "marginalia catalog",
            "scan": "marginalia scan <vault>",
            "tags": "marginalia tags <vault>",
            "fix": "marginalia fix <vault> --apply",
            "discover": "marginalia discover <vault>",
            "link": "marginalia link <vault> --apply --no-what-if",
        },
        "discoveries": {
            "tag_affinity": discoveries["tag_affinity"]["suggestions"][:5],
            "orphan_homes": discoveries["orphan_homes"]["suggestions"][:5],
            "cluster_bridges": discoveries["cluster_bridges"]["suggestions"][:5],
        },
    }
    return blueprint


def render_catalog_text():
    """Render a concise human-readable capability catalog."""
    grouped = {}
    for item in CATALOG:
        grouped.setdefault(item["category"], []).append(item)

    lines = [
        f"marginalia {__version__} -- Operator Catalog",
        "=" * 50,
        "Goal-first entrypoints for operators:",
    ]
    for category in sorted(grouped):
        lines.append(f"\n[{category}]")
        for item in grouped[category]:
            lines.append(f"  - {item['goal']}")
            lines.append(f"    {item['command']}")
            lines.append(f"    {item['when_to_use']}")
    return "\n".join(lines)


def render_quickstart_text(blueprint):
    """Render a quickstart blueprint for terminal output."""
    summary = blueprint["summary"]
    lines = [
        f"marginalia {__version__} -- Operator Quickstart",
        "=" * 50,
        f"Vault:          {blueprint['vault']}",
        f"Files:          {summary['files']}",
        f"Scan issues:    {summary['scan_issues']}",
        f"Obsidian issues:{summary['obsidian_issues']}",
        f"Orphans:        {summary['orphans']}",
        f"Flat tags:      {summary['flat_tags']}",
        f"Stage:          {summary['operator_stage']}",
        "",
        "Recommended flow:",
    ]
    for step in blueprint["recommended_flow"]:
        lines.append(f"  [{step['priority']}] {step['title']}")
        lines.append(f"    {step['command']}")
        lines.append(f"    {step['why']}")
    return "\n".join(lines)


def render_quickstart_markdown(blueprint):
    """Render the materialized blueprint as Markdown."""
    summary = blueprint["summary"]
    lines = [
        "# marginalia Operator Blueprint",
        "",
        f"- Generated: `{blueprint['timestamp']}`",
        f"- Vault: `{blueprint['vault']}`",
        f"- Stage: `{summary['operator_stage']}`",
        "",
        "## Summary",
        "",
        f"- Files: {summary['files']}",
        f"- Scan issues: {summary['scan_issues']}",
        f"- Obsidian issues: {summary['obsidian_issues']}",
        f"- Orphans: {summary['orphans']}",
        f"- Flat tags: {summary['flat_tags']}",
        f"- Tag affinity candidates: {summary['tag_affinity_candidates']}",
        "",
        "## Recommended Flow",
        "",
    ]
    for idx, step in enumerate(blueprint["recommended_flow"], start=1):
        lines.append(f"{idx}. **{step['title']}**")
        lines.append(f"   - Command: `{step['command']}`")
        lines.append(f"   - Why: {step['why']}")
    if blueprint["top_issues"]:
        lines.extend([
            "",
            "## Top Issues",
            "",
        ])
        for issue in blueprint["top_issues"]:
            lines.append(f"- `{issue['type']}`: {issue['count']}")
    return "\n".join(lines) + "\n"


def materialize_quickstart(blueprint, output_dir):
    """Write JSON and Markdown blueprint artifacts to disk."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "operator-blueprint.json"
    md_path = out_dir / "operator-blueprint.md"
    json_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_quickstart_markdown(blueprint), encoding="utf-8")
    return {
        "json": str(json_path).replace("\\", "/"),
        "markdown": str(md_path).replace("\\", "/"),
    }
