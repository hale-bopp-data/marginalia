"""CLI entry point for marginalia — 14 commands: scan, check, fix, fix-tags, discover, index, css, graph, link, eval, ai, closeout, session-close, validate."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .scanner import (find_md_files, scan_file, build_file_index, build_graph,
                      tag_issues, untag_issues, REVIEW_TAG, build_tag_dictionary,
                      build_tag_inventory, build_synonym_map_from_inventory,
                      rationalize_tags, parse_frontmatter)
from .tags import load_taxonomy, fix_tags_in_file, validate_taxonomy
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

    # --tag: add review tag to files with issues
    if getattr(args, "tag", False) and all_issues:
        tag_result = tag_issues(primary_vault, all_issues, dry_run=False)
        if args.json:
            result["tag_result"] = tag_result
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        else:
            print(f"\n--- Tagged for review ---")
            print(f"  Files tagged:  {tag_result['tagged']}")
            print(f"  Already tagged: {tag_result['already']}")
            print(f"  Skipped:       {tag_result['skipped']}")

    # --strict: CI guardrail — fail if missing_domain_tag exceeds threshold
    strict_threshold = getattr(args, "strict", None)
    if strict_threshold is not None:
        domain_issues = by_type.get("missing_domain_tag", 0)
        if domain_issues > strict_threshold:
            if not args.json:
                print(f"\n--- STRICT MODE FAILED ---")
                print(f"  missing_domain_tag: {domain_issues} (threshold: {strict_threshold})")
                print(f"  Fix: marginalia fix <vault> --giri 6 --taxonomy <taxonomy.yml> --apply")
            sys.exit(1)

    # Obsidian tip (always, if issues found and not JSON)
    if all_issues and not args.json:
        print(f"\n--- Find in Obsidian ---")
        print(f"  Search: tag:{REVIEW_TAG}")
        if not getattr(args, "tag", False):
            print(f"  (run with --tag to auto-tag files with issues)")
        print(f"  When fixed, run: marginalia untag <vault>")

    sys.exit(0 if not all_issues else 1)


def cmd_tags(args):
    vault = _ensure_vault(args.vault)
    out_path = args.out
    analyze = getattr(args, "analyze", False)
    taxonomy = getattr(args, "taxonomy", None)

    # --- Taxonomy validation (if provided) ---
    if taxonomy:
        tax_issues = validate_taxonomy(taxonomy)
        if tax_issues:
            print(f"\n--- Taxonomy validation: {len(tax_issues)} issues ---", file=sys.stderr)
            for ti in tax_issues:
                print(f"  [{ti['type']}] {ti['detail']}", file=sys.stderr)
            print(file=sys.stderr)
        else:
            print(f"Taxonomy validated: {taxonomy} (no issues)", file=sys.stderr)

    rationalize = getattr(args, "rationalize", False)

    if rationalize:
        # --- Global LLM rationalization: full landscape analysis ---
        print(f"marginalia {__version__} -- Tag Rationalization (LLM Global)\n{'=' * 50}", file=sys.stderr)
        print(f"Vault: {vault}", file=sys.stderr)
        print(f"Analyzing full tag landscape...\n", file=sys.stderr)

        result = rationalize_tags(vault, taxonomy_path=taxonomy)

        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)

        if out_path:
            Path(out_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        else:
            # Domain merges
            dm = result.get("domain_merges", [])
            if dm:
                print(f"--- Domain merges ({len(dm)}) ---", file=sys.stderr)
                for m in dm:
                    print(f"  {m.get('from','?'):35s} -> {m.get('to','?'):25s}  {m.get('reason','')[:50]}", file=sys.stderr)
                print(file=sys.stderr)

            # Namespace merges
            nm = result.get("namespace_merges", [])
            if nm:
                print(f"--- Namespace merges ({len(nm)}) ---", file=sys.stderr)
                for m in nm:
                    print(f"  {m.get('from','?'):35s} -> {m.get('to','?'):25s}  {m.get('reason','')[:50]}", file=sys.stderr)
                print(file=sys.stderr)

            # Flat assignments
            fa = result.get("flat_assignments", [])
            if fa:
                print(f"--- Flat tag assignments ({len(fa)}) ---", file=sys.stderr)
                for a in fa[:20]:
                    print(f"  {a.get('tag','?'):25s} -> {a.get('to','?'):25s}  {a.get('reason','')[:50]}", file=sys.stderr)
                if len(fa) > 20:
                    print(f"  ... +{len(fa) - 20} more", file=sys.stderr)
                print(file=sys.stderr)

            # Proposed YAML
            yml = result.get("proposed_yaml_merges", "")
            if yml:
                print(f"--- Proposed taxonomy merges (add to taxonomy.yml) ---", file=sys.stderr)
                print(yml, file=sys.stderr)

            if out_path:
                print(f"\nFull analysis saved to: {out_path}", file=sys.stderr)

            print(f"\nNext steps:", file=sys.stderr)
            print(f"  1. Review proposals above", file=sys.stderr)
            print(f"  2. Add accepted merges to taxonomy.yml", file=sys.stderr)
            print(f"  3. Run: marginalia fix-tags <vault> --taxonomy <yml> --apply", file=sys.stderr)
            print(f"  4. Run: marginalia fix <vault> --giri 6 --taxonomy <yml> --apply", file=sys.stderr)
        sys.exit(0)

    if analyze:
        # --- Full LLM analysis: per-page tag suggestion with reasoning ---
        print(f"marginalia {__version__} -- Tag Inventory (LLM Analysis)\n{'=' * 50}", file=sys.stderr)
        print(f"Vault: {vault}", file=sys.stderr)
        if taxonomy:
            print(f"Taxonomy: {taxonomy}", file=sys.stderr)
        print(f"Analyzing pages...\n", file=sys.stderr)

        def _progress(cur, total, name):
            if cur % 10 == 0 or cur == total:
                print(f"  [{cur}/{total}] {name}", file=sys.stderr)

        inventory = build_tag_inventory(vault, taxonomy_path=taxonomy, progress_cb=_progress)

        if isinstance(inventory, dict) and "error" in inventory:
            print(f"ERROR: {inventory['error']}", file=sys.stderr)
            sys.exit(1)

        # Build synonym map from inventory
        synonym_map = build_synonym_map_from_inventory(inventory)

        result = {
            "action": "marginalia-tag-inventory", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vault": str(vault),
            "pages_analyzed": len(inventory),
            "unique_tags_suggested": len(synonym_map),
            "inventory": inventory,
            "synonym_map": synonym_map,
        }

        if out_path:
            Path(out_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        else:
            print(f"\nPages analyzed: {len(inventory)}", file=sys.stderr)
            print(f"Unique tags suggested: {len(synonym_map)}", file=sys.stderr)

            # Show top suggested tags with reasons
            if synonym_map:
                print(f"\n--- Top suggested tags (with reasoning) ---", file=sys.stderr)
                for entry in synonym_map[:20]:
                    reasons = "; ".join(entry["reasons"][:2]) if entry["reasons"] else "(no reason)"
                    print(f"  {entry['tag']:30s} {entry['count']:3d} pages  |  {reasons[:80]}", file=sys.stderr)
                if len(synonym_map) > 20:
                    print(f"  ... and {len(synonym_map) - 20} more", file=sys.stderr)

            if out_path:
                print(f"\nInventory written to: {out_path}", file=sys.stderr)
            else:
                print(f"\nRun with --out <path> to save inventory", file=sys.stderr)

            print(f"\nNext steps:", file=sys.stderr)
            print(f"  1. Review inventory: tags with similar reasons = synonyms", file=sys.stderr)
            print(f"  2. Add merges to taxonomy.yml", file=sys.stderr)
            print(f"  3. Run: marginalia fix-tags <vault> --taxonomy <taxonomy.yml> --apply", file=sys.stderr)
        sys.exit(0)

    # --- Fast mode: read existing frontmatter (no LLM) ---
    result = build_tag_dictionary(vault)

    if out_path:
        out_data = {
            "action": "marginalia-tag-dictionary", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vault": str(vault),
            "total": result["total"],
            "namespaced": result["namespaced"],
            "flat": result["flat"],
            "tags": result["tags"],
            "synonym_groups": result["synonym_groups"],
        }
        Path(out_path).write_text(
            json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Tag Dictionary (L0)\n{'=' * 50}")
        print(f"Vault: {vault}")
        print(f"Total tags:   {result['total']}")
        print(f"  Namespaced: {result['namespaced']}")
        print(f"  Flat:       {result['flat']}  (need resolution)\n")

        flat_tags = [e for e in result["tags"] if e["namespace"] is None]
        if flat_tags:
            print(f"--- Top flat tags ({len(flat_tags)} total, need namespace) ---")
            for e in flat_tags[:20]:
                print(f"  {e['tag']:30s} {e['count']:3d} files")
            if len(flat_tags) > 20:
                print(f"  ... and {len(flat_tags) - 20} more")
            print()

        if result["synonym_groups"]:
            print(f"--- Synonym candidates ({len(result['synonym_groups'])}) ---")
            for g in result["synonym_groups"][:15]:
                cands = ", ".join(g["candidates"])
                print(f"  {g['flat_tag']:20s} ({g['flat_count']} files) -> {cands}")
            if len(result["synonym_groups"]) > 15:
                print(f"  ... and {len(result['synonym_groups']) - 15} more")
            print()

        # S155: Smart pre-filtering results
        auto_resolved = result.get("auto_resolved", [])
        pattern_merges = result.get("pattern_merges", [])
        prune_candidates = result.get("prune_candidates", [])

        if auto_resolved:
            print(f"--- Auto-resolvable ({len(auto_resolved)}) --- [single synonym match, no LLM needed]")
            for a in auto_resolved[:15]:
                print(f"  {a['flat_tag']:25s} -> {a['resolved_to']:25s}  [{a['rule']}]")
            if len(auto_resolved) > 15:
                print(f"  ... and {len(auto_resolved) - 15} more")
            print()

        if pattern_merges:
            print(f"--- Pattern merges ({len(pattern_merges)}) --- [rule-based, no LLM needed]")
            for p in pattern_merges[:15]:
                print(f"  {p['flat_tag']:25s} -> {p['resolved_to']:25s}  [{p['rule']}]")
            if len(pattern_merges) > 15:
                print(f"  ... and {len(pattern_merges) - 15} more")
            print()

        if prune_candidates:
            print(f"--- Prune candidates ({len(prune_candidates)}) --- [1 file, no match, low value]")
            for p in prune_candidates[:10]:
                print(f"  {p['flat_tag']:25s}  in {p['file'][:50]}")
            if len(prune_candidates) > 10:
                print(f"  ... and {len(prune_candidates) - 10} more")
            print()

        # Summary: how much LLM work is eliminated
        remaining = result["flat"] - len(auto_resolved) - len(pattern_merges) - len(prune_candidates)
        if auto_resolved or pattern_merges or prune_candidates:
            print(f"--- Smart filtering summary ---")
            print(f"  Flat tags:         {result['flat']}")
            print(f"  Auto-resolved:     {len(auto_resolved)}  (apply with --auto-resolve)")
            print(f"  Pattern merges:    {len(pattern_merges)}  (apply with --auto-resolve)")
            print(f"  Prune candidates:  {len(prune_candidates)}  (review with --prune)")
            print(f"  Remaining for LLM: {remaining}")
            print()

        if out_path:
            print(f"Dictionary written to: {out_path}")
        else:
            print("Run with --out <path> to write tag-dictionary.json")

        # Coverage: count missing_domain_tag from a quick scan
        from .scanner import extract_tags as _extract_tags
        _all_files = find_md_files(vault)
        no_domain = 0
        active_files = 0
        for _f in _all_files:
            _rel = str(_f.relative_to(vault)).replace("\\", "/")
            if "template" in _rel.lower() or "/archive/" in _rel or _rel.startswith("archive/"):
                continue
            active_files += 1
            try:
                _content = _f.read_text(encoding="utf-8", errors="replace")
                _fm = parse_frontmatter(_content)
                if _fm:
                    _tags = _extract_tags(_fm)
                    if not any(t.startswith("domain/") for t in _tags):
                        no_domain += 1
                else:
                    no_domain += 1
            except Exception:
                pass
        pct = round(no_domain / max(active_files, 1) * 100)
        print(f"--- RAG coverage ---")
        print(f"  Files without domain/ tag: {no_domain}/{active_files} ({pct}%)")
        if pct > 20:
            print(f"  WARNING: {pct}% of files invisible to RAG domain routing")
        print()

        print(f"For LLM-powered analysis with reasoning: marginalia tags <vault> --analyze")
        print(f"\nNext steps:")
        print(f"  1. Review flat tags and synonym candidates above")
        print(f"  2. Add merges to taxonomy.yml")
        print(f"  3. Run: marginalia fix-tags <vault> --taxonomy <taxonomy.yml> --apply")
    sys.exit(0)


def cmd_untag(args):
    vault = _ensure_vault(args.vault)
    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "APPLIED"
    cleaned = untag_issues(vault, dry_run=dry_run)
    if args.json:
        print(json.dumps({"action": "marginalia-untag", "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(), "vault": str(vault),
            "mode": mode, "files_cleaned": cleaned, "tag": REVIEW_TAG,
        }, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Untag Review ({mode})\n{'=' * 50}")
        print(f"Vault: {vault}")
        print(f"Tag removed: {REVIEW_TAG}")
        print(f"Files cleaned: {cleaned}")
        if dry_run:
            print("\nRun with --apply to remove tags.")
    sys.exit(0)


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


def _print_fix_report(result, vault):
    """Print a detailed fix report with per-action, per-directory, and per-target stats."""
    inv = result["giri"].get("0_inventory", {})
    total_files = inv.get("total_files", 0)
    with_fm = inv.get("with_frontmatter", 0)
    without_fm = inv.get("without_frontmatter", 0)
    pct = (with_fm * 100 // total_files) if total_files else 0

    print(f"marginalia {__version__} -- Fix Pipeline ({result['mode']})")
    print("=" * 56)
    print(f"Vault: {vault}\n")
    print(f"  Total files:           {total_files}")
    print(f"  With frontmatter:      {with_fm} ({pct}%)")
    print(f"  Without frontmatter:   {without_fm}")
    print(f"  Proposed fixes:        {result['total_fixes']}")
    print()

    # --- Giro 1: Frontmatter ---
    g1 = result["giri"].get("1_frontmatter", {})
    details = g1.get("details", [])
    if details or g1.get("fixes"):
        actions = {}
        dirs = {}
        for d in details:
            a = d.get("action", "unknown")
            actions[a] = actions.get(a, 0) + 1
            f = d.get("file", "")
            dr = f.split("/")[0] if "/" in f else "(root)"
            dirs[dr] = dirs.get(dr, 0) + 1
        print(f"-- Giro 1: FRONTMATTER ({g1.get('fixes', len(details))} fixes) --")
        print("  By action:")
        for a, c in sorted(actions.items(), key=lambda x: -x[1]):
            print(f"    {a:30s} {c:4d}")
        print("  By directory (top 10):")
        for d, c in sorted(dirs.items(), key=lambda x: -x[1])[:10]:
            print(f"    {d + '/':30s} {c:4d}")
        print()

    # --- Giro 2: Tags ---
    g2 = result["giri"].get("2_tags", {})
    details = g2.get("details", [])
    if details or g2.get("fixes"):
        migrations = {}
        for d in details:
            for old, new in d.get("changes", {}).items():
                k = f"{old} -> {new}"
                migrations[k] = migrations.get(k, 0) + 1
        print(f"-- Giro 2: TAGS ({g2.get('fixes', len(details))} fixes) --")
        print("  Migrations:")
        for m, c in sorted(migrations.items(), key=lambda x: -x[1]):
            print(f"    {m:40s} {c:4d}")
        print()

    # --- Giro 3: Links ---
    g3 = result["giri"].get("3_links", {})
    details = g3.get("details", [])
    if details or g3.get("fixes"):
        link_targets = {}
        dirs3 = {}
        relinks = 0
        removals = 0
        for d in details:
            f = d.get("file", "")
            dr = f.split("/")[0] if "/" in f else "(root)"
            dirs3[dr] = dirs3.get(dr, 0) + 1
            for ch in d.get("changes", []):
                old = ch.get("old", "")
                fname = old.split("/")[-1] if "/" in old else old
                link_targets[fname] = link_targets.get(fname, 0) + 1
                if ch.get("new"):
                    relinks += 1
                else:
                    removals += 1
        print(f"-- Giro 3: LINKS ({g3.get('fixes', len(details))} fixes) --")
        print("  Fix type:")
        if relinks:
            print(f"    {'relink (path corrected)':40s} {relinks:4d}")
        if removals:
            print(f"    {'remove (target missing)':40s} {removals:4d}")
        print("  Broken targets (top 10):")
        for t, c in sorted(link_targets.items(), key=lambda x: -x[1])[:10]:
            print(f"    {t:40s} {c:4d}")
        print("  Files fixed by directory (top 10):")
        for d, c in sorted(dirs3.items(), key=lambda x: -x[1])[:10]:
            print(f"    {d + '/':30s} {c:4d}")
        print()

    # --- Giro 4: Obsidian ---
    g4 = result["giri"].get("4_obsidian", {})
    details = g4.get("details", [])
    if details or g4.get("fixes"):
        print(f"-- Giro 4: OBSIDIAN ({g4.get('fixes', len(details))} fixes) --")
        for d in details:
            print(f"  {d.get('file', '?')}: {d.get('action', '?')}")
        print()

    # --- Giro 5: Wikilinks ---
    g5 = result["giri"].get("5_wikilinks", {})
    details = g5.get("details", [])
    if details or g5.get("fixes"):
        strategies = {}
        targets = {}
        dirs5 = {}
        for d in details:
            f = d.get("file", "")
            dr = f.split("/")[0] if "/" in f else "(root)"
            dirs5[dr] = dirs5.get(dr, 0) + 1
            for fix in d.get("details", []):
                s = fix.get("strategy", "unknown")
                strategies[s] = strategies.get(s, 0) + 1
                t = fix.get("target", "")
                targets[t] = targets.get(t, 0) + 1
        print(f"-- Giro 5: WIKILINKS ({g5.get('fixes', len(details))} files, "
              f"{sum(strategies.values())} fixes) --")
        print("  By strategy:")
        for s, c in sorted(strategies.items(), key=lambda x: -x[1]):
            print(f"    {s:30s} {c:4d}")
        print("  Top broken targets:")
        for t, c in sorted(targets.items(), key=lambda x: -x[1])[:10]:
            label = t[:38] if len(t) > 38 else t
            print(f"    {label:40s} {c:4d}")
        print("  Files fixed by directory (top 10):")
        for d, c in sorted(dirs5.items(), key=lambda x: -x[1])[:10]:
            print(f"    {d + '/':30s} {c:4d}")
        print()

    # --- Summary ---
    all_files = set()
    for gname in ["1_frontmatter", "2_tags", "3_links", "5_wikilinks"]:
        for d in result["giri"].get(gname, {}).get("details", []):
            all_files.add(d.get("file", ""))
    deleted = len(result["giri"].get("4_obsidian", {}).get("details", []))
    print("=" * 56)
    print("SUMMARY")
    print(f"  Files touched:         ~{len(all_files)}")
    if deleted:
        print(f"  Files deleted:         {deleted}")
    print(f"  Total operations:      {result['total_fixes']}")
    # Find heaviest giro
    heaviest = ""
    heaviest_n = 0
    for gname in ["1_frontmatter", "2_tags", "3_links", "4_obsidian", "5_wikilinks"]:
        n = result["giri"].get(gname, {}).get("fixes", 0)
        if n > heaviest_n:
            heaviest_n = n
            heaviest = gname
    if heaviest:
        print(f"  Heaviest giro:         {heaviest} ({heaviest_n})")
    print()
    if result["mode"] == "DRY RUN":
        print("Run with --apply to execute changes.")


def cmd_fix(args):
    from .fixer import fix_all
    vault = _ensure_vault(args.vault)
    giri = [int(g) for g in args.giri.split(",")] if args.giri else None
    result = fix_all(vault, dry_run=not args.apply, taxonomy_path=args.taxonomy,
                     required_fields=args.require.split(",") if args.require else None, giri=giri)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        _print_fix_report(result, vault)
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


def cmd_closeout(args):
    from .closeout import run_closeout
    from .validators import validate_closeout
    base_dir = Path(args.base).resolve() if args.base else Path.cwd()
    sessions_history = args.sessions_history or None

    result = run_closeout(
        base_dir=base_dir,
        session_number=args.session_number,
        session_title=args.title,
        write=args.write,
        use_ai=args.ai,
        model=args.model,
        sessions_history_path=sessions_history,
    )

    # Validate output against acceptance criteria
    validation = validate_closeout(result)
    result["validation"] = validation

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"marginalia {__version__} -- Session Closeout ({result['mode']})\n{'=' * 50}")
        print(f"Session: S{result['session_number']}")
        print(f"Date:    {result['date']}")
        print(f"Title:   {result['title']}")
        print(f"AI:      {'yes' if result['ai_used'] else 'no'}")
        print(f"Repos:   {', '.join(result['repos_scanned'])}")
        print(f"Commits: {result['commits_found']}")
        print(f"PRs:     {', '.join(f'#{p}' for p in result['prs']) or '(none)'}\n")

        if result["files_written"]:
            print("Files written:")
            for f in result["files_written"]:
                print(f"  {f}")
        else:
            print("--- Platform Memory Entry ---")
            print(result["template"]["platform_memory_entry"][:500])
            print("\n--- Chronicle ---")
            print(f"  File: {result['template']['chronicle_filename']}")
            print(f"  Preview: {result['template']['chronicle_content'][:200]}...")

            if result["mode"] == "DRY RUN":
                print("\nRun with --write to create files.")

        # Print validation report
        v = result.get("validation", {})
        if v:
            status = "PASS" if v["valid"] else "FAIL"
            print(f"\n--- Validation ({status}, confidence={v['confidence']:.0%}) ---")
            if v.get("failed"):
                for f in v["failed"]:
                    print(f"  FAIL: {f['id']} — {f['description']}")
            if v["valid"]:
                print(f"  All {v['total_checks']} acceptance criteria passed.")
    sys.exit(0)


def cmd_session_close(args):
    from .session_close import run_session_close
    base_dir = Path(args.base).resolve() if args.base else Path.cwd()
    sessions_history = args.sessions_history or None

    result = run_session_close(
        base_dir=base_dir,
        session_number=args.session_number,
        session_title=args.title,
        write=args.write,
        use_ai=args.ai,
        model=args.model,
        sessions_history_path=sessions_history,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        mode = result["mode"]
        print(f"marginalia {__version__} -- Session Close ({mode})\n{'=' * 60}")
        print(f"Session: S{result['session_number']}")
        print(f"Date:    {result['date']}")
        print(f"Title:   {result['title']}\n")

        # Checklist with status icons
        icons = {"done": "\u2705", "dry-run": "\U0001f4cb", "manual": "\U0001f449",
                 "action-needed": "\U0001f6a8", "warning": "\u26a0\ufe0f",
                 "skipped": "\u23ed\ufe0f", "clean": "\u2705"}
        print("--- Checklist 9 punti ---")
        for item in result["checklist"]:
            icon = icons.get(item["status"], "\u2753")
            detail = f" — {item['detail']}" if item.get("detail") else ""
            print(f"  {icon} [{item['step']}] {item['name']}: {item['status']}{detail}")

        # Summary
        s = result["summary"]
        print(f"\nRisultato: {s['done']} done, {s['manual']} manual, "
              f"{s['action_needed']} action-needed, {s['warnings']} warnings")

        # Dirty repos detail
        if result.get("dirty_repos"):
            print("\n--- Repo con modifiche non committate ---")
            for repo, info in result["dirty_repos"].items():
                print(f"  {repo}: {info['count']} files")
                for f in info.get("files", [])[:5]:
                    print(f"    {f}")

        # Unpushed repos detail
        if result.get("unpushed_repos"):
            print("\n--- Repo con commit non pushati ---")
            for repo, info in result["unpushed_repos"].items():
                print(f"  {repo}: {info['count']} commits")
                for c in info.get("commits", [])[:3]:
                    print(f"    {c}")

        # Handoff
        if result.get("handoff"):
            print(f"\n{'=' * 60}")
            print(result["handoff"])

        if mode == "DRY RUN":
            print("\nRun with --write to execute. Manual steps remain manual.")

    sys.exit(0)


def cmd_validate(args):
    from .validators import validate_closeout, validate_scan
    if args.input == "-":
        data = json.load(sys.stdin)
    else:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)

    validator = validate_closeout if args.type == "closeout" else validate_scan
    report = validator(data)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    else:
        status = "PASS" if report["valid"] else "FAIL"
        print(f"marginalia validate ({args.type}) — {status}")
        print(f"Confidence: {report['confidence']:.0%} ({len(report['passed'])}/{report['total_checks']} checks)\n")
        for p in report["passed"]:
            print(f"  PASS  {p['id']}: {p['description']}")
        for f in report["failed"]:
            print(f"  FAIL  {f['id']}: {f['description']}")
        if not report["valid"]:
            print(f"\nRequires human review: {len(report['failed'])} criteria failed.")
    sys.exit(0 if report["valid"] else 1)


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
    p.add_argument("--tag", action="store_true", help="Add quality/review-needed tag to files with issues (for Obsidian filtering)")
    p.add_argument("--strict", type=int, metavar="N", help="Exit code 1 if missing_domain_tag > N (CI guardrail, e.g. --strict 0)")

    p = sub.add_parser("tags", help="Tag Dictionary (L0): inventory all tags, detect synonyms, write dictionary")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--out", "-o", help="Output path for tag-dictionary.json")
    p.add_argument("--analyze", action="store_true", help="LLM analysis: read each page, suggest tags with reasoning (requires API key)")
    p.add_argument("--rationalize", action="store_true", help="LLM global rationalization: propose taxonomy merges across all tags")
    p.add_argument("--taxonomy", help="Taxonomy YAML for canonical value hints during analysis")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("untag", help="Remove quality/review-needed tags after manual review")
    p.add_argument("vault", nargs="?", default=".")
    p.add_argument("--apply", action="store_true", help="Apply (default: dry-run)")
    p.add_argument("--json", action="store_true")

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

    p = sub.add_parser("closeout", help="Session closeout: collect git data, generate reports, write files")
    p.add_argument("session_number", type=int, help="Session number (e.g. 103)")
    p.add_argument("--title", help="Session title (auto-generated from commits if omitted)")
    p.add_argument("--base", help="Base directory of polyrepo (default: cwd)")
    p.add_argument("--write", action="store_true", help="Write files (default: dry-run preview)")
    p.add_argument("--ai", action="store_true", help="Use LLM to generate narrative (requires API key)")
    p.add_argument("--model", help="LLM model override")
    p.add_argument("--sessions-history", help="Path to sessions-history.md")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("session-close", help="Full 9-point session closeout orchestrator (wraps closeout + checks)")
    p.add_argument("session_number", type=int, help="Session number (e.g. 141)")
    p.add_argument("--title", help="Session title (auto-generated from commits if omitted)")
    p.add_argument("--base", help="Base directory of polyrepo (default: cwd)")
    p.add_argument("--write", action="store_true", help="Write files (default: dry-run preview)")
    p.add_argument("--ai", action="store_true", help="Use LLM to generate narrative (requires API key)")
    p.add_argument("--model", help="LLM model override")
    p.add_argument("--sessions-history", help="Path to sessions-history.md")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("validate", help="Validate a JSON output against acceptance criteria (Evaluator pattern)")
    p.add_argument("input", help="Path to JSON file or - for stdin")
    p.add_argument("--type", choices=["closeout", "scan"], default="closeout", help="Validation type (default: closeout)")
    p.add_argument("--json", action="store_true")

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

    cmds = {"scan": cmd_scan, "tags": cmd_tags, "untag": cmd_untag, "check": cmd_check, "fix": cmd_fix,
            "fix-tags": cmd_fix_tags, "discover": cmd_discover, "index": cmd_index,
            "css": cmd_css, "graph": cmd_graph, "link": cmd_link, "eval": cmd_eval,
            "ai": cmd_ai, "closeout": cmd_closeout, "session-close": cmd_session_close,
            "validate": cmd_validate}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
