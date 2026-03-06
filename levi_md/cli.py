"""CLI entry point for levi-md."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .scanner import find_md_files, scan_file, build_file_index, build_graph
from .tags import load_taxonomy, fix_tags_in_file
from .obsidian import check_all as obsidian_check_all

# Force UTF-8 on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def cmd_scan(args):
    """Scan vault for quality issues."""
    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)

    file_index = build_file_index(vault)
    md_files = find_md_files(vault)

    required = args.require.split(",") if args.require else ["title", "tags"]

    all_issues = []
    for f in md_files:
        issues = scan_file(f, vault, file_index=file_index, required_fields=required)
        all_issues.extend(issues)

    # Build graph
    graph = build_graph(vault, file_index)

    by_type = {}
    for issue in all_issues:
        by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1

    result = {
        "action": "levi-scan",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vault": str(vault),
        "files_scanned": len(md_files),
        "issues_found": len(all_issues),
        "issues_by_type": by_type,
        "issues": all_issues,
        "graph": graph,
        "status": "clean" if not all_issues else "issues_found",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} — Vault Scan Report")
        print("=" * 50)
        print(f"Vault:    {vault}")
        print(f"Files:    {len(md_files)}")
        print(f"Issues:   {len(all_issues)}")
        print()
        topo = graph["topology"]
        print(f"--- Tags ---")
        print(f"  Total:      {graph['tag_count']} ({graph['namespaced_tags']} namespaced, {graph['flat_tags']} flat)")
        print(f"--- Links ---")
        print(f"  Resolved:   {graph['link_count']}")
        if topo["hubs"]:
            print(f"  Top hub:    {topo['hubs'][0]['file']} ({topo['hubs'][0]['outgoing']} outgoing)")
        if topo["authorities"]:
            print(f"  Top auth:   {topo['authorities'][0]['file']} ({topo['authorities'][0]['inbound']} inbound)")
        print(f"  Orphans:    {graph['orphan_count']}")
        print()
        if by_type:
            print("Issues by type:")
            for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"  {t}: {count}")
            print()
        if all_issues:
            print("Top issues:")
            for issue in all_issues[:20]:
                print(f"  [{issue['type']}] {issue['file']}:{issue['line']} — {issue['description']}")
            if len(all_issues) > 20:
                print(f"  ... and {len(all_issues) - 20} more")
        else:
            print("No issues found. Vault is clean!")

    sys.exit(0 if not all_issues else 1)


def cmd_check(args):
    """Run Obsidian-specific health checks."""
    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)

    issues = obsidian_check_all(vault, max_depth=args.max_depth)

    if args.json:
        print(json.dumps({
            "action": "levi-check-obsidian",
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vault": str(vault),
            "issues_found": len(issues),
            "issues": issues,
            "status": "clean" if not issues else "issues_found",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} — Obsidian Health Check")
        print("=" * 50)
        print(f"Vault: {vault}")
        print(f"Issues: {len(issues)}")
        print()
        by_type = {}
        for i in issues:
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
        if by_type:
            print("Issues by type:")
            for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"  {t}: {count}")
            print()
        for issue in issues:
            fix = issue.get("fix", "")
            print(f"  [{issue['type']}] {issue['file']} — {issue['description']}")
            if fix:
                print(f"    Fix: {fix}")
        if not issues:
            print("Vault is Obsidian-healthy!")

    sys.exit(0 if not issues else 1)


def cmd_fix_tags(args):
    """Migrate flat tags to namespaced taxonomy."""
    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)

    namespaces, merges, case_fixes = load_taxonomy(args.taxonomy)
    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "APPLYING"

    total, changed = 0, 0
    all_changes = {}
    for f in find_md_files(vault):
        total += 1
        changes = fix_tags_in_file(f, dry_run=dry_run,
                                    namespaces=namespaces, merges=merges, case_fixes=case_fixes)
        if changes:
            changed += 1
            for old, new in changes.items():
                key = f"{old} -> {new}"
                all_changes[key] = all_changes.get(key, 0) + 1

    if args.json:
        print(json.dumps({
            "action": "levi-fix-tags",
            "version": __version__,
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vault": str(vault),
            "files_scanned": total,
            "files_changed": changed,
            "tag_changes": all_changes,
            "status": "applied" if not dry_run else "dry_run",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"levi-md {__version__} — Tag Migration ({mode})")
        print("=" * 50)
        print(f"Files scanned: {total}")
        print(f"Files changed: {changed}")
        print(f"Unique tag changes: {len(all_changes)}")
        print()
        for change, count in sorted(all_changes.items(), key=lambda x: -x[1]):
            print(f"  {change}  ({count} files)")
        if dry_run:
            print(f"\nRun with --apply to execute changes.")

    sys.exit(0)


def cmd_graph(args):
    """Output vault relationship graph as JSON."""
    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)

    graph = build_graph(vault)
    print(json.dumps(graph, ensure_ascii=False, indent=2), flush=True)
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        prog="levi",
        description="levi-md — Markdown vault quality scanner for Obsidian and documentation teams"
    )
    parser.add_argument("--version", action="version", version=f"levi-md {__version__}")
    sub = parser.add_subparsers(dest="command")

    # scan
    p_scan = sub.add_parser("scan", help="Scan vault for quality issues")
    p_scan.add_argument("vault", nargs="?", default=".", help="Path to vault (default: current dir)")
    p_scan.add_argument("--json", action="store_true", help="JSON output")
    p_scan.add_argument("--require", help="Required frontmatter fields (comma-separated, default: title,tags)")

    # check (Obsidian-specific)
    p_check = sub.add_parser("check", help="Obsidian-specific health checks")
    p_check.add_argument("vault", nargs="?", default=".", help="Path to vault (default: current dir)")
    p_check.add_argument("--json", action="store_true", help="JSON output")
    p_check.add_argument("--max-depth", type=int, default=5, help="Max directory depth (default: 5)")

    # fix-tags
    p_tags = sub.add_parser("fix-tags", help="Migrate flat tags to namespaced taxonomy")
    p_tags.add_argument("vault", nargs="?", default=".", help="Path to vault (default: current dir)")
    p_tags.add_argument("--taxonomy", help="Path to taxonomy YAML file")
    p_tags.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    p_tags.add_argument("--json", action="store_true", help="JSON output")

    # graph
    p_graph = sub.add_parser("graph", help="Output vault relationship graph as JSON")
    p_graph.add_argument("vault", nargs="?", default=".", help="Path to vault (default: current dir)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "scan": cmd_scan,
        "check": cmd_check,
        "fix-tags": cmd_fix_tags,
        "graph": cmd_graph,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
