"""Multi-pass vault fixer — the 4 Giri (rounds) with actual repairs.

Giro 0: Inventory — build file index, parse all frontmatter, snapshot state
Giro 1: Frontmatter — add missing frontmatter, fill required fields
Giro 2: Tags — migrate flat→namespaced, merge duplicates, case normalize
Giro 3: Links — fix stale markdown links using file index suggestions
Giro 4: Obsidian — fix .gitignore, remove Untitled.canvas, wikilink repair

Each giro reads the state left by the previous one (no stale data).
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime, timezone

from .scanner import find_md_files, parse_frontmatter, extract_tags, build_file_index, suggest_correct_path
from .tags import load_taxonomy, migrate_tag, fix_tags_in_file


def _read_file(filepath):
    try:
        return filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _write_file(filepath, content):
    filepath.write_text(content, encoding="utf-8")


# --- Giro 0: Inventory ---

def giro0_inventory(vault_path):
    """Build the base: file index, frontmatter map, stats."""
    base = Path(vault_path)
    file_index = build_file_index(base)
    md_files = find_md_files(base)

    fm_map = {}  # rel_path → fm_fields
    for f in md_files:
        content = _read_file(f)
        if content:
            rel = str(f.relative_to(base)).replace("\\", "/")
            fm = parse_frontmatter(content)
            fm_map[rel] = fm  # None if no frontmatter

    return {
        "vault": str(base),
        "file_index": file_index,
        "md_files": md_files,
        "fm_map": fm_map,
        "total_files": len(md_files),
        "with_frontmatter": sum(1 for v in fm_map.values() if v is not None),
        "without_frontmatter": sum(1 for v in fm_map.values() if v is None),
    }


# --- Giro 1: Frontmatter ---

def giro1_frontmatter(vault_path, inventory, required_fields=None, dry_run=True):
    """Add missing frontmatter to files that don't have it.

    Strategy:
    - Files with NO frontmatter: add minimal --- title/tags --- block
    - Title is derived from filename (kebab-case → Title Case)
    - Tags left empty for user to fill
    """
    required_fields = required_fields or ["title", "tags"]
    base = Path(vault_path)
    fixes = []

    for f in inventory["md_files"]:
        rel = str(f.relative_to(base)).replace("\\", "/")
        content = _read_file(f)
        if content is None:
            continue

        fm = parse_frontmatter(content)

        if fm is None:
            # Generate frontmatter from filename
            stem = f.stem
            title = stem.replace("-", " ").replace("_", " ").title()
            new_fm = f"---\ntitle: \"{title}\"\ntags: []\n---\n\n"
            new_content = new_fm + content

            fixes.append({
                "file": rel,
                "action": "add_frontmatter",
                "title": title,
            })

            if not dry_run:
                _write_file(f, new_content)

        elif fm is not None:
            # Check for missing required fields — add them
            missing = [field for field in required_fields if field not in fm]
            if missing:
                # Insert missing fields into existing frontmatter
                fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
                if fm_match:
                    fm_text = fm_match.group(2)
                    additions = []
                    for field in missing:
                        if field == "title":
                            val = f.stem.replace("-", " ").replace("_", " ").title()
                            additions.append(f'title: "{val}"')
                        elif field == "tags":
                            additions.append("tags: []")
                        elif field == "status":
                            additions.append("status: active")
                        else:
                            additions.append(f"{field}: ")

                    new_fm_text = fm_text + "\n" + "\n".join(additions)
                    new_content = content.replace(fm_match.group(0),
                                                   f"{fm_match.group(1)}{new_fm_text}{fm_match.group(3)}", 1)

                    fixes.append({
                        "file": rel,
                        "action": "add_missing_fields",
                        "fields": missing,
                    })

                    if not dry_run:
                        _write_file(f, new_content)

    return fixes


# --- Giro 2: Tags ---

def giro2_tags(vault_path, inventory, taxonomy_path=None, dry_run=True):
    """Migrate flat tags to namespaced. Wraps tags.fix_tags_in_file with inventory context."""
    base = Path(vault_path)
    namespaces, merges, case_fixes = load_taxonomy(taxonomy_path)
    fixes = []

    for f in inventory["md_files"]:
        rel = str(f.relative_to(base)).replace("\\", "/")
        changes = fix_tags_in_file(f, dry_run=dry_run,
                                    namespaces=namespaces, merges=merges, case_fixes=case_fixes)
        if changes:
            fixes.append({
                "file": rel,
                "action": "migrate_tags",
                "changes": changes,
            })

    return fixes


# --- Giro 3: Links ---

def giro3_links(vault_path, inventory, dry_run=True):
    """Fix stale markdown links using file index suggestions.

    Only fixes links where we have a confident suggestion (file exists
    in the vault with the same name). Broken links with no suggestion
    are left untouched (reported but not fixed).
    """
    base = Path(vault_path)
    file_index = inventory["file_index"]
    fixes = []

    for f in inventory["md_files"]:
        rel = str(f.relative_to(base)).replace("\\", "/")
        content = _read_file(f)
        if content is None:
            continue

        new_content = content
        file_fixes = []

        # Process markdown links [text](target) — reverse order to preserve positions
        matches = list(re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content))
        for m in reversed(matches):
            link_text, link_target = m.group(1), m.group(2)
            if re.match(r"^(https?://|mailto:|#)", link_target):
                continue
            link_path = link_target.split("#")[0]
            anchor = link_target[len(link_path):]  # preserve #anchor
            if not link_path:
                continue

            resolved = f.parent / link_path
            if not resolved.exists():
                suggested_rel, suggested_full = suggest_correct_path(f, link_path, file_index)
                if suggested_rel:
                    new_link = f"[{link_text}]({suggested_rel}{anchor})"
                    new_content = new_content[:m.start()] + new_link + new_content[m.end():]
                    file_fixes.append({
                        "old": link_target,
                        "new": suggested_rel + anchor,
                    })

        if file_fixes:
            fixes.append({
                "file": rel,
                "action": "fix_links",
                "count": len(file_fixes),
                "changes": file_fixes,
            })
            if not dry_run:
                _write_file(f, new_content)

    return fixes


# --- Giro 4: Obsidian ---

def giro4_obsidian(vault_path, inventory, dry_run=True):
    """Fix Obsidian-specific issues: .gitignore, canvas, wikilink conversion."""
    base = Path(vault_path)
    fixes = []

    # Fix .gitignore
    gitignore = base / ".gitignore"
    if (base / ".git").exists():
        recommended = [".obsidian/", ".trash/", "*.canvas"]
        if not gitignore.exists():
            content = "\n".join(recommended) + "\n"
            fixes.append({
                "file": ".gitignore",
                "action": "create_gitignore",
                "entries": recommended,
            })
            if not dry_run:
                _write_file(gitignore, content)
        else:
            existing = _read_file(gitignore) or ""
            missing = [r for r in recommended if r not in existing]
            if missing:
                new_content = existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n"
                fixes.append({
                    "file": ".gitignore",
                    "action": "update_gitignore",
                    "added": missing,
                })
                if not dry_run:
                    _write_file(gitignore, new_content)

    # Remove Untitled.canvas
    for canvas in base.rglob("Untitled.canvas"):
        rel = str(canvas.relative_to(base)).replace("\\", "/")
        fixes.append({
            "file": rel,
            "action": "remove_untitled_canvas",
        })
        if not dry_run:
            canvas.unlink()

    return fixes


# --- Orchestrator: run all giri ---

def fix_all(vault_path, dry_run=True, taxonomy_path=None, required_fields=None,
            giri=None):
    """Run the multi-pass fix pipeline.

    Args:
        vault_path: Path to the Markdown vault
        dry_run: If True, only report what would change (default: True)
        taxonomy_path: Optional path to taxonomy YAML
        required_fields: Frontmatter fields to require
        giri: List of giro numbers to run (default: [0,1,2,3,4] = all)

    Returns:
        Dict with results per giro and summary stats.
    """
    if giri is None:
        giri = [0, 1, 2, 3, 4]

    results = {
        "action": "levi-fix",
        "mode": "DRY RUN" if dry_run else "APPLIED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vault": str(vault_path),
        "giri": {},
    }

    # Giro 0 always runs (inventory)
    inventory = giro0_inventory(vault_path)
    results["giri"]["0_inventory"] = {
        "total_files": inventory["total_files"],
        "with_frontmatter": inventory["with_frontmatter"],
        "without_frontmatter": inventory["without_frontmatter"],
    }

    total_fixes = 0

    if 1 in giri:
        g1 = giro1_frontmatter(vault_path, inventory, required_fields=required_fields, dry_run=dry_run)
        results["giri"]["1_frontmatter"] = {"fixes": len(g1), "details": g1}
        total_fixes += len(g1)
        # Re-inventory after frontmatter changes (giro 2 needs fresh FM data)
        if not dry_run and g1:
            inventory = giro0_inventory(vault_path)

    if 2 in giri:
        g2 = giro2_tags(vault_path, inventory, taxonomy_path=taxonomy_path, dry_run=dry_run)
        results["giri"]["2_tags"] = {"fixes": len(g2), "details": g2}
        total_fixes += len(g2)

    if 3 in giri:
        # Re-build file index (in case giro 1 added new files or changed paths)
        if not dry_run:
            inventory = giro0_inventory(vault_path)
        g3 = giro3_links(vault_path, inventory, dry_run=dry_run)
        results["giri"]["3_links"] = {"fixes": len(g3), "details": g3}
        total_fixes += len(g3)

    if 4 in giri:
        g4 = giro4_obsidian(vault_path, inventory, dry_run=dry_run)
        results["giri"]["4_obsidian"] = {"fixes": len(g4), "details": g4}
        total_fixes += len(g4)

    results["total_fixes"] = total_fixes
    results["status"] = "applied" if not dry_run else "dry_run"

    return results
