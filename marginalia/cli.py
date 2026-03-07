"""CLI entry point for marginalia — 10 commands: scan, check, fix, fix-tags, discover, index, css, graph, link, ai."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .scanner import find_md_files, scan_file, build_file_index, build_graph
from .tags import load_taxonomy, fix_tags_in_file
from .obsidian import check_all as obsidian_check_all
from .config import load_config, find_config, merge_cli

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _ensure_vault(vault_str):
    vault = Path(vault_str).resolve()
    if not vault.is_dir():
        print(f"ERROR: Not a directory: {vault}", file=sys.stderr)
        sys.exit(1)
    return vault


def _ensure_vaults(vault_list):
    """Validate and resolve a list of vault paths."""
    vaults = []
    for v in vault_list:
        p = Path(v).resolve()
        if not p.is_dir():
            print(f"ERROR: Not a directory: {p}", file=sys.stderr)
            sys.exit(1)
        vaults.append(p)
    return vaults


def _load_cfg(args, vault_hint=None):
    """Load marginalia.yaml, searching near vault(s) and cwd."""
    config_path = getattr(args, "config", None)
    search_dirs = [Path.cwd()]
    if vault_hint:
        if isinstance(vault_hint, (str, Path)):
            search_dirs.append(Path(vault_hint))
        else:
            search_dirs.extend(Path(v) for v in vault_hint)
    return load_config(config_path=config_path, search_dirs=search_dirs)


def cmd_scan(args):
    # Multi-vault: args.vaults is a list (nargs="+")
    vaults = _ensure_vaults(args.vaults)
    cfg = _load_cfg(args, vault_hint=vaults)
    # Config vaults only apply when no CLI vaults provided (default ".")
    if args.vaults == ["."]:
        cfg_vaults = cfg.get("vaults", [])
        if cfg_vaults:
            vaults = _ensure_vaults(cfg_vaults)

    required = args.require.split(",") if args.require else ["title", "tags"]
    all_issues = []

    # For single vault, use existing graph; for multi, scan each separately
    primary_vault = vaults[0]
    file_index = build_file_index(primary_vault)
    md_files = find_md_files(vaults)

    for f in md_files:
        # Determine the vault this file belongs to for relative path calculation
        owning_vault = primary_vault
        for v in vaults:
            try:
                f.relative_to(v)
                owning_vault = v
                break
            except ValueError:
                continue
        all_issues.extend(scan_file(f, owning_vault, file_index=file_index, required_fields=required))

    graph = build_graph(primary_vault, file_index)
    by_type = {}
    for issue in all_issues:
        by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1

    vault_strs = [str(v) for v in vaults]
    result = {
        "action": "marginalia-scan", "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vaults": vault_strs, "files_scanned": len(md_files),
        "issues_found": len(all_issues), "issues_by_type": by_type,
        "issues": all_issues, "graph": graph,
        "status": "clean" if not all_issues else "issues_found",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        topo = graph["topology"]
        print(f"marginalia {__version__} -- Vault Scan Report\n{'=' * 50}")
        vault_label = ", ".join(str(v) for v in vaults)
        print(f"Vault(s): {vault_label}\nFiles:    {len(md_files)}\nIssues:   {len(all_issues)}\n")
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
        print(json.dumps({"action": "marginalia-check-obsidian", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault),
            "issues_found": len(issues), "issues": issues,
            "status": "clean" if not issues else "issues_found",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Obsidian Health Check\n{'=' * 50}")
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
        print(f"marginalia {__version__} -- Fix Pipeline ({result['mode']})\n{'=' * 50}")
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
        print(json.dumps({"action": "marginalia-fix-tags", "version": __version__, "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault),
            "files_scanned": total, "files_changed": changed, "tag_changes": all_changes,
            "status": "applied" if not dry_run else "dry_run",
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Tag Migration ({mode})\n{'=' * 50}")
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
        print(json.dumps({"action": "marginalia-discover", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault), **result,
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Connection Discovery\n{'=' * 50}\n")
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
    output_dir = Path(args.output) if args.output else vault / "_marginalia"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"marginalia {__version__} -- Index Generator\n{'=' * 50}")
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
    output = vault / ".obsidian" / "snippets" / "marginalia-tag-colors.css"
    if args.output:
        output = Path(args.output)
    generate_obsidian_css_snippet(output_file=output)
    print(f"marginalia {__version__} -- Tag Color CSS Snippet\n{'=' * 50}")
    print(f"Output: {output}\n\nTag colors:")
    for ns, color in sorted(TAG_COLORS.items()):
        print(f"  {ns}: {color}")
    print("\nEnable in Obsidian: Settings > Appearance > CSS Snippets > marginalia-tag-colors")
    sys.exit(0)


def cmd_graph(args):
    vault = _ensure_vault(args.vault)
    print(json.dumps(build_graph(vault), ensure_ascii=False, indent=2), flush=True)
    sys.exit(0)


def cmd_link(args):
    from .linker import run_link
    vaults = _ensure_vaults(args.vaults)
    cfg = _load_cfg(args, vault_hint=vaults)
    # Config vaults only apply when no CLI vaults provided (default ".")
    if args.vaults == ["."]:
        cfg_vaults = cfg.get("vaults", [])
        if cfg_vaults:
            vaults = _ensure_vaults(cfg_vaults)

    # Merge config with CLI args (CLI wins)
    exclude_cli = [e.strip() for e in args.exclude.split(",")] if args.exclude else []
    exclude = list(dict.fromkeys(cfg.get("exclude", []) + exclude_cli))
    min_score = args.min_score if args.min_score != 0.35 else cfg.get("min_score", 0.35)
    max_links = args.max_links if args.max_links != 5 else cfg.get("max_links", 5)
    top_k = args.top_k if args.top_k != 7 else cfg.get("top_k", 7)
    heading = args.heading if args.heading != "## See also" else cfg.get("heading", "## See also")

    out_json = args.out or "out/marginalia-suggestions.json"
    apply = args.apply
    what_if = not args.no_what_if  # default: what_if=True (dry-run)

    result = run_link(
        vault_path=vaults if len(vaults) > 1 else vaults[0],
        out_json=out_json,
        exclude=exclude,
        top_k=top_k,
        min_score=min_score,
        max_links=max_links,
        scope=args.scope,
        apply=apply,
        what_if=what_if,
        heading=heading,
        link_graph_json=args.link_graph,
        apply_out_dir=args.apply_out,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Related Links\n{'=' * 50}")
        vault_label = ", ".join(str(v) for v in vaults)
        print(f"Vault(s): {vault_label}")
        print(f"Docs:   {result['docs']}")
        print(f"Output: {out_json}\n")

        # Show top suggestions preview
        shown = 0
        for entry in result["results"]:
            if not entry["suggestions"]:
                continue
            top = entry["suggestions"][0]
            if top["score"] < args.min_score:
                continue
            print(f"  {entry['path']}")
            for s in entry["suggestions"][:3]:
                if s["score"] >= args.min_score:
                    print(f"    -> {s['path']}  (score={s['score']:.3f})")
            shown += 1
            if shown >= 20:
                remaining = sum(1 for e in result["results"] if e["suggestions"] and e["suggestions"][0]["score"] >= args.min_score) - shown
                if remaining > 0:
                    print(f"  ... and {remaining} more")
                break

        if apply:
            ap = result.get("apply", {})
            mode = "DRY RUN" if what_if else "APPLIED"
            print(f"\n--- Apply ({mode}) ---")
            print(f"Scope:   {ap.get('scope')}")
            print(f"Targets: {ap.get('targets')}")
            print(f"Changed: {ap.get('changed')}")
            if what_if:
                print("\nRun with --apply --no-what-if to write files.")
            else:
                print(f"Backups: {ap.get('backupDir')}")
        else:
            print("\nRun with --apply to add See Also sections (--no-what-if to actually write).")
    sys.exit(0)


def cmd_eval(args):
    from .eval import run_snapshot, run_compare
    action = getattr(args, "action", None)
    if not action:
        print("Usage: marginalia eval snapshot [VAULT] --queries queries.yaml\n"
              "       marginalia eval compare --before baseline.json --after after.json", file=sys.stderr)
        sys.exit(1)

    if action == "snapshot":
        vaults = _ensure_vaults(args.vaults)
        cfg = _load_cfg(args, vault_hint=vaults)
        if args.vaults == ["."]:
            cfg_vaults = cfg.get("vaults", [])
            if cfg_vaults:
                vaults = _ensure_vaults(cfg_vaults)

        queries_file = args.queries
        if not queries_file:
            # Auto-discover: look for queries.yaml in vault or cwd
            for candidate in [Path.cwd() / "queries.yaml", vaults[0] / "queries.yaml",
                               Path.cwd() / "eval-queries.yaml"]:
                if candidate.exists():
                    queries_file = str(candidate)
                    break
        if not queries_file:
            print("ERROR: --queries <file> required (or place queries.yaml in vault/cwd)", file=sys.stderr)
            sys.exit(1)

        out = args.out or "out/marginalia-eval-snapshot.json"
        try:
            result = run_snapshot(
                vault_path=vaults if len(vaults) > 1 else vaults[0],
                queries_file=queries_file,
                out_path=out,
                top_k=args.top_k,
                min_score=args.min_score,
            )
        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        else:
            s = result["summary"]
            print(f"marginalia {__version__} -- Eval Snapshot\n{'=' * 50}")
            print(f"Vault(s): {', '.join(str(v) for v in vaults)}")
            print(f"Docs:     {result['docs']}")
            print(f"Queries:  {s['queries']}")
            print(f"Coverage: {s['coverage']:.0%}  ({s['queries_with_results']}/{s['queries']} queries returned results)")
            print(f"Avg top-1 score: {s['avg_top1_score']:.3f}")
            print(f"Avg mean score:  {s['avg_mean_score']:.3f}")
            if "precision_at_k" in s:
                print(f"Precision@{args.top_k}: {s['precision_at_k']:.3f}")
                print(f"Recall@{args.top_k}:    {s['recall_at_k']:.3f}")
            print(f"\nSnapshot saved: {out}")
            print("\nTop queries by score:")
            sorted_q = sorted(result["queries"], key=lambda q: -q["metrics"]["top1_score"])
            for q in sorted_q[:10]:
                top = q["results"][0]["path"] if q["results"] else "(no results)"
                print(f"  [{q['metrics']['top1_score']:.3f}] {q['text'][:60]}")
                print(f"         → {top}")

    elif action == "compare":
        if not args.before or not args.after:
            print("ERROR: --before <snapshot.json> --after <snapshot.json> required", file=sys.stderr)
            sys.exit(1)
        try:
            result = run_compare(args.before, args.after)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        else:
            agg = result["aggregate"]
            verdict = agg.get("verdict", "N/A")
            verdict_icon = {"IMPROVED": "↑", "DEGRADED": "↓", "NEUTRAL": "→"}.get(verdict, "?")
            print(f"marginalia {__version__} -- Eval Compare\n{'=' * 50}")
            print(f"Verdict: {verdict_icon} {verdict}")
            print(f"Queries compared: {agg.get('queries_compared', 0)}")
            print(f"Avg top-1 score delta: {agg.get('avg_top1_score_delta', 0):+.3f}")
            print(f"Avg mean score delta:  {agg.get('avg_mean_score_delta', 0):+.3f}")
            print(f"Coverage delta:        {agg.get('coverage_delta', 0):+.0%}")
            if "avg_precision_delta" in agg:
                print(f"Precision delta:       {agg['avg_precision_delta']:+.3f}")
                print(f"Recall delta:          {agg['avg_recall_delta']:+.3f}")

            print("\nQuery-level changes (top 15 by abs delta):")
            compared = [d for d in result["queries"] if d.get("status") == "compared"]
            compared.sort(key=lambda d: -abs(d.get("top1_score_delta", 0)))
            for d in compared[:15]:
                delta = d.get("top1_score_delta", 0)
                icon = "↑" if delta > 0.01 else ("↓" if delta < -0.01 else "→")
                new = len(d.get("new_results", []))
                lost = len(d.get("lost_results", []))
                extras = f"  [+{new} new, -{lost} lost]" if new or lost else ""
                print(f"  {icon} {delta:+.3f}  {d['text'][:60]}{extras}")

    sys.exit(0)


def cmd_ai(args):
    from . import brain
    vault = _ensure_vault(args.vault)
    if not brain.is_available():
        print("ERROR: No API key configured.", file=sys.stderr)
        print("Set one of: MARGINALIA_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY\n", file=sys.stderr)
        print("OpenRouter:  export OPENROUTER_API_KEY=sk-or-v1-...")
        print("Ollama:      export MARGINALIA_API_KEY=ollama MARGINALIA_API_URL=http://localhost:11434/v1 MARGINALIA_MODEL=llama3")
        sys.exit(1)

    if args.action == "review":
        print(f"marginalia {__version__} -- AI Vault Review\n{'=' * 50}")
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
    parser = argparse.ArgumentParser(prog="marginalia",
        description="marginalia -- Markdown vault scanner, fixer, and AI brain for Obsidian")
    parser.add_argument("--version", action="version", version=f"marginalia {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="Scan one or more vaults for quality issues")
    p.add_argument("vaults", nargs="*", default=["."], metavar="VAULT",
                   help="Vault path(s) to scan (default: ., or vaults from marginalia.yaml)")
    p.add_argument("--config", help="Path to marginalia.yaml (auto-discovered if omitted)")
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

    p = sub.add_parser("link", help="Suggest related links via TF-IDF cosine similarity")
    p.add_argument("vaults", nargs="*", default=["."], metavar="VAULT",
                   help="Vault path(s) (default: ., or vaults from marginalia.yaml)")
    p.add_argument("--config", help="Path to marginalia.yaml (auto-discovered if omitted)")
    p.add_argument("--out", "-o", help="Output JSON path (default: out/marginalia-suggestions.json)")
    p.add_argument("--exclude", help="Comma-separated vault-relative paths to exclude")
    p.add_argument("--top-k", type=int, default=7, help="Top-K candidates per document (default: 7)")
    p.add_argument("--min-score", type=float, default=0.35, help="Minimum score threshold (default: 0.35)")
    p.add_argument("--max-links", type=int, default=5, help="Max links to add per file (default: 5)")
    p.add_argument("--scope", choices=["all", "orphans-only"], default="all")
    p.add_argument("--apply", action="store_true", help="Enable apply phase (still dry-run unless --no-what-if)")
    p.add_argument("--no-what-if", action="store_true", help="Actually write files (requires --apply)")
    p.add_argument("--heading", default="## See also", help="See Also heading to insert/append under")
    p.add_argument("--link-graph", help="Path to link-graph JSON (for orphans-only scope)")
    p.add_argument("--apply-out", help="Directory for backups (default: <vault>/out/marginalia-link-apply)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("eval", help="Before/after RAG quality measurement")
    eval_sub = p.add_subparsers(dest="action")

    ps = eval_sub.add_parser("snapshot", help="Build a query-response quality snapshot")
    ps.add_argument("vaults", nargs="*", default=["."], metavar="VAULT")
    ps.add_argument("--queries", "-q", help="Path to queries.yaml (auto-discovered if omitted)")
    ps.add_argument("--out", "-o", help="Output snapshot JSON path (default: out/marginalia-eval-snapshot.json)")
    ps.add_argument("--top-k", type=int, default=5, help="Top-K results per query (default: 5)")
    ps.add_argument("--min-score", type=float, default=0.10, help="Min score to include result (default: 0.10)")
    ps.add_argument("--config", help="Path to marginalia.yaml")
    ps.add_argument("--json", action="store_true")

    pc = eval_sub.add_parser("compare", help="Compare two snapshots and show quality delta")
    pc.add_argument("--before", required=True, help="Path to baseline snapshot JSON")
    pc.add_argument("--after", required=True, help="Path to after snapshot JSON")
    pc.add_argument("--json", action="store_true")

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
            "link": cmd_link, "eval": cmd_eval, "ai": cmd_ai}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
