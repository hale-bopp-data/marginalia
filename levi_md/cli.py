"""CLI entry point for levi-md — 9 commands: scan, check, fix, fix-tags, discover, index, css, graph, ai."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .scanner import find_md_files, scan_file, build_file_index, build_graph
from .tags import load_taxonomy, fix_tags_in_file
from .obsidian import check_all as obsidian_check_all

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _ensure_vault(vault_str):
    vault = Path(vault_str).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)
    return vault


def cmd_scan(args):
    vault = _ensure_vault(args.vault)
    file_index = build_file_index(vault)
    md_files = find_md_files(vault)
    required = args.require.split(",") if args.require else ["title", "tags"]
    all_issues = []
    for f in md_files:
        all_issues.extend(scan_file(f, vault, file_index=file_index, required_fields=required))
    graph = build_graph(vault, file_index)
    by_type = {}
    for issue in all_issues:
        by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1
    result = {"action": "levi-scan", "version": __version__,
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "vault": str(vault), "files_scanned": len(md_files),
              "issues_found": len(all_issues), "issues_by_type": by_type,
              "issues": all_issues, "graph": graph,
              "status": "clean" if not all_issues else "issues_found"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        topo = graph["topology"]
        print(f"levi-md {__version__} -- Vault Scan Report\n{'=' * 50}")
        print(f"Vault:    {vault}\nFiles:    {len(md_files)}\nIssues:   {len(all_issues)}\n")
        print(f"--- Tags ---\n  Total: {graph['tag_count']} ({graph['namespaced_tags']} namespaced, {graph['flat_tags']} flat)")
        print(f"--- Links ---\n  Resolved: {graph['link_count']}")
        if topo["hubs"]:
            print(f"  Top hub:  {topo['hubs'][0]['file']} ({topo['hubs'][0]['outgoing']} out)")
        if topo["authorities"]:
            print(f"  Top auth: {topo['authorities'][0]['file']} ({topo['authorities'][0]['inbound']} in)")
        print(f"  Orphans:  {graph['orphan_count']}\n")
        if by_type:
            print("Issues by type:")
            for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"  {t}: {count}")
            print()
        for issue in all_issues[:20]:
            print(f"  [{issue['type']}] {issue['file']}:{issue['line']} -- {issue['description']}")
        if len(all_issues) > 20:
            print(f"  ... and {len(all_issues) - 20} more")
        if not all_issues:
            print("No issues found. Vault is clean!")
    sys.exit(0 if not all_issues else 1)


def cmd_check(args):
    vault = _ensure_vault(args.vault)
    issues = obsidian_check_all(vault, max_depth=args.max_depth)
    if args.json:
        print(json.dumps({"action": "levi-check-obsidian", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault),
            "issues_found": len(issues), "issues": issues,
            "status": "clean" if not issues else "issues_found",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} -- Obsidian Health Check\n{'=' * 50}")
        print(f"Vault: {vault}\nIssues: {len(issues)}\n")
        by_type = {}
        for i in issues:
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
        if by_type:
            for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"  {t}: {count}")
            print()
        for issue in issues:
            print(f"  [{issue['type']}] {issue['file']} -- {issue['description']}")
            if issue.get("fix"):
                print(f"    Fix: {issue['fix']}")
        if not issues:
            print("Vault is Obsidian-healthy!")
    sys.exit(0 if not issues else 1)


def cmd_fix(args):
    from .fixer import fix_all
    vault = _ensure_vault(args.vault)
    giri = [int(g) for g in args.giri.split(",")] if args.giri else None
    result = fix_all(vault, dry_run=not args.apply, taxonomy_path=args.taxonomy,
                     required_fields=args.require.split(",") if args.require else None, giri=giri)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} -- Fix Pipeline ({result['mode']})\n{'=' * 50}")
        print(f"Vault: {vault}\n")
        for giro_name, giro_data in result["giri"].items():
            if isinstance(giro_data, dict) and "fixes" in giro_data:
                print(f"Giro {giro_name}: {giro_data['fixes']} fixes")
                for detail in giro_data.get("details", [])[:10]:
                    action = detail.get("action", "")
                    f = detail.get("file", "")
                    if "changes" in detail and isinstance(detail["changes"], dict):
                        ch = ", ".join(f"{k}->{v}" for k, v in list(detail["changes"].items())[:3])
                        print(f"  {f}: {action} ({ch})")
                    elif "count" in detail:
                        print(f"  {f}: {action} ({detail['count']} links)")
                    else:
                        print(f"  {f}: {action}")
                extra = len(giro_data.get("details", [])) - 10
                if extra > 0:
                    print(f"  ... and {extra} more")
            elif isinstance(giro_data, dict):
                for k, v in giro_data.items():
                    print(f"  {k}: {v}")
            print()
        print(f"Total fixes: {result['total_fixes']}")
        if result["mode"] == "DRY RUN":
            print("\nRun with --apply to execute changes.")
    sys.exit(0)


def cmd_fix_tags(args):
    vault = _ensure_vault(args.vault)
    namespaces, merges, case_fixes = load_taxonomy(args.taxonomy)
    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "APPLYING"
    total, changed, all_changes = 0, 0, {}
    for f in find_md_files(vault):
        total += 1
        changes = fix_tags_in_file(f, dry_run=dry_run, namespaces=namespaces,
                                    merges=merges, case_fixes=case_fixes)
        if changes:
            changed += 1
            for old, new in changes.items():
                key = f"{old} -> {new}"
                all_changes[key] = all_changes.get(key, 0) + 1
    if args.json:
        print(json.dumps({"action": "levi-fix-tags", "version": __version__, "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault),
            "files_scanned": total, "files_changed": changed, "tag_changes": all_changes,
            "status": "applied" if not dry_run else "dry_run",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} -- Tag Migration ({mode})\n{'=' * 50}")
        print(f"Files scanned: {total}\nFiles changed: {changed}\nUnique changes: {len(all_changes)}\n")
        for change, count in sorted(all_changes.items(), key=lambda x: -x[1]):
            print(f"  {change}  ({count} files)")
        if dry_run:
            print("\nRun with --apply to execute changes.")
    sys.exit(0)


def cmd_discover(args):
    from .discovery import discover_all
    vault = _ensure_vault(args.vault)
    result = discover_all(vault, min_shared_tags=args.min_tags, max_results=args.max_results)
    if args.json:
        print(json.dumps({"action": "levi-discover", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault), **result,
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} -- Connection Discovery\n{'=' * 50}\n")
        for section_key, section in result.items():
            print(f"--- {section['description']} ---")
            print(f"  Found: {section['count']}")
            for s in section["suggestions"][:10]:
                if "source" in s:
                    print(f"  {s['source']} <-> {s['target']}")
                    print(f"    {s['reason']}: {s.get('shared_tags', s.get('detail', ''))}")
                elif "orphan" in s:
                    print(f"  {s['orphan']} -> link from {s['suggested_parent']}")
                    conf = s.get("confidence", 0)
                    print(f"    {s['reason']} (confidence: {conf:.0%})")
                elif "file" in s:
                    domains = ", ".join(s.get("domains", [])[:5])
                    print(f"  {s['file']} bridges: {domains}")
            extra = section["count"] - 10
            if extra > 0:
                print(f"  ... and {extra} more")
            print()
    sys.exit(0)


def cmd_index(args):
    from .index_builder import build_master_index, build_tag_index, build_orphan_index
    vault = _ensure_vault(args.vault)
    output_dir = Path(args.output) if args.output else vault / "_levi"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"levi-md {__version__} -- Index Generator\n{'=' * 50}")
    print(f"Vault: {vault}\nOutput: {output_dir}\n")
    build_master_index(vault, output_file=output_dir / "vault-index.md")
    print("  Created: vault-index.md (master MOC)")
    tag_pages = build_tag_index(vault, output_dir=output_dir)
    for name in tag_pages:
        print(f"  Created: {name}")
    build_orphan_index(vault, output_file=output_dir / "orphans.md")
    print("  Created: orphans.md")
    print(f"\nTotal: {2 + len(tag_pages)} index pages in {output_dir}/")
    sys.exit(0)


def cmd_css(args):
    from .index_builder import generate_obsidian_css_snippet, TAG_COLORS
    vault = _ensure_vault(args.vault)
    output = vault / ".obsidian" / "snippets" / "levi-tag-colors.css"
    if args.output:
        output = Path(args.output)
    generate_obsidian_css_snippet(output_file=output)
    print(f"levi-md {__version__} -- Tag Color CSS Snippet\n{'=' * 50}")
    print(f"Output: {output}\n\nTag colors:")
    for ns, color in sorted(TAG_COLORS.items()):
        print(f"  {ns}: {color}")
    print("\nEnable in Obsidian: Settings > Appearance > CSS Snippets > levi-tag-colors")
    sys.exit(0)


def cmd_graph(args):
    vault = _ensure_vault(args.vault)
    print(json.dumps(build_graph(vault), ensure_ascii=False, indent=2), flush=True)
    sys.exit(0)


def cmd_ai(args):
    from . import brain
    vault = _ensure_vault(args.vault)
    if not brain.is_available():
        print("ERROR: No API key configured.", file=sys.stderr)
        print("Set one of: LEVI_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY\n", file=sys.stderr)
        print("OpenRouter:  export OPENROUTER_API_KEY=sk-or-v1-...")
        print("Ollama:      export LEVI_API_KEY=ollama LEVI_API_URL=http://localhost:11434/v1 LEVI_MODEL=llama3")
        sys.exit(1)

    if args.action == "review":
        print(f"levi-md {__version__} -- AI Vault Review\n{'=' * 50}")
        print(f"Vault: {vault}\nModel: {args.model or 'default'}\n")
        print(brain.review_vault(vault, sample_size=args.sample or 10) or "No response.")
    elif args.action in ("tag", "connect", "frontmatter"):
        if not args.file:
            print("ERROR: --file required for tag/connect/frontmatter", file=sys.stderr)
            sys.exit(1)
        fp = Path(args.file).resolve()
        if not fp.exists():
            print(f"ERROR: File not found: {fp}", file=sys.stderr)
            sys.exit(1)
        if args.action == "tag":
            tags = brain.suggest_tags(fp, taxonomy_hint=["domain", "artifact", "process", "tech", "meta"])
            if tags:
                print(f"Suggested tags: {tags}")
            else:
                print("Could not generate suggestions.")
        elif args.action == "connect":
            conns = brain.suggest_connections(fp, vault)
            if conns:
                for c in conns:
                    print(f"  -> {c.get('file', '?')}: {c.get('reason', '')}")
            else:
                print("Could not generate suggestions.")
        elif args.action == "frontmatter":
            fm = brain.generate_frontmatter(fp)
            print(fm if fm else "Could not generate frontmatter.")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(prog="levi",
        description="levi-md -- Markdown vault scanner, fixer, and AI brain for Obsidian")
    parser.add_argument("--version", action="version", version=f"levi-md {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="Scan vault for quality issues")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--require", help="Required frontmatter fields (comma-separated)")

    p = sub.add_parser("check", help="Obsidian health checks")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--max-depth", type=int, default=5)

    p = sub.add_parser("fix", help="Multi-pass fix pipeline (4 Giri): frontmatter, tags, links, obsidian")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    p.add_argument("--taxonomy", help="Path to taxonomy YAML")
    p.add_argument("--require", help="Required frontmatter fields")
    p.add_argument("--giri", help="Which giri to run (e.g. 1,2,3)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fix-tags", help="Migrate flat tags to namespaced")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--taxonomy")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("discover", help="Find hidden connections between notes")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--min-tags", type=int, default=2, help="Min shared tags for affinity")
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("index", help="Generate index pages (MOC, tags, orphans)")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--output", help="Output directory")

    p = sub.add_parser("css", help="Generate Obsidian CSS for colored tags")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--output")

    p = sub.add_parser("graph", help="Output relationship graph as JSON")
    p.add_argument("vault", nargs="?", default=".")

    p = sub.add_parser("ai", help="AI-powered analysis (OpenRouter/OpenAI/Ollama)")
    p.add_argument("action", choices=["review", "tag", "connect", "frontmatter"])
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--file", help="Target file (for tag/connect/frontmatter)")
    p.add_argument("--model", help="LLM model override")
    p.add_argument("--sample", type=int, help="Sample size for review")
    p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    cmds = {"scan": cmd_scan, "check": cmd_check, "fix": cmd_fix, "fix-tags": cmd_fix_tags,
            "discover": cmd_discover, "index": cmd_index, "css": cmd_css, "graph": cmd_graph,
            "ai": cmd_ai}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
