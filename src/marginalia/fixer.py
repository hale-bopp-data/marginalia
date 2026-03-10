"""Multi-pass vault fixer — the 5 Giri (rounds) with actual repairs.

Giro 0: Inventory — build file index, parse all frontmatter, snapshot state
Giro 1: Frontmatter — add missing frontmatter, fill required fields
Giro 2: Tags — migrate flat→namespaced, merge duplicates, case normalize
Giro 3: Links — fix stale markdown links using file index suggestions
Giro 4: Obsidian — fix .gitignore, remove Untitled.canvas
Giro 5: Wikilinks — resolve, tag-convert, or unwrap broken [[wikilinks]]

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


# --- Giro 5: Wikilinks ---

# Default tag-convert patterns: [[Pattern - Value]] → tag namespace/value
_TAG_CONVERT_PATTERNS = {
    "Layer": "layer",
    "Domain": "domain",
    "Audience": "audience",
    "Status": "status",
    "Type": "type",
}


def _parse_wikilink_tag(target, patterns):
    """Check if a wikilink target matches a tag-convert pattern.

    Returns (namespace, value) if match, else (None, None).
    Example: "Layer - Index" → ("layer", "index")
    """
    for prefix, namespace in patterns.items():
        if target.startswith(prefix + " - ") or target.startswith(prefix + " — "):
            sep = " - " if " - " in target else " — "
            value = target.split(sep, 1)[1].strip().lower().replace(" ", "-")
            return namespace, value
    return None, None


def _add_tag_to_frontmatter(content, tag):
    """Add a tag to the frontmatter tags list if not already present."""
    fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not fm_match:
        return content

    fm_text = fm_match.group(2)

    # Find tags line
    tags_match = re.search(r"^(tags:\s*)\[([^\]]*)\]", fm_text, re.MULTILINE)
    if tags_match:
        existing_tags = [t.strip().strip("'\"") for t in tags_match.group(2).split(",") if t.strip()]
        if tag in existing_tags:
            return content
        existing_tags.append(tag)
        new_tags_line = f"{tags_match.group(1)}[{', '.join(existing_tags)}]"
        new_fm_text = fm_text[:tags_match.start()] + new_tags_line + fm_text[tags_match.end():]
        return content[:fm_match.start(2)] + new_fm_text + content[fm_match.end(2):]

    # No tags line — add one
    new_fm_text = fm_text + f"\ntags: [{tag}]"
    return content[:fm_match.start(2)] + new_fm_text + content[fm_match.end(2):]


def giro5_wikilinks(vault_path, inventory, tag_patterns=None, dry_run=True):
    """Fix broken wikilinks with 3 strategies (in priority order):

    1. resolve — find the target file in the vault, rewrite the path
    2. tag-convert — convert "Layer - X" / "Domain - X" patterns to frontmatter tags
    3. unwrap — remove [[ ]] markup but keep visible text (last resort)

    Never silently deletes content. Unwrap always preserves the display text.
    """
    if tag_patterns is None:
        tag_patterns = _TAG_CONVERT_PATTERNS

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

        # Find all wikilinks [[target]] or [[target|display]]
        wikilink_re = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
        matches = list(wikilink_re.finditer(content))

        for m in reversed(matches):
            target = m.group(1).strip()
            display = m.group(2)  # may be None

            # Skip if target resolves to existing file
            target_path = target
            if not target_path.endswith(".md"):
                target_path += ".md"

            # Check validity the same way the scanner does: by filename in file_index.
            # This ensures fixer and scanner agree on what is "broken".
            key = Path(target_path).name.lower()
            scanner_key_full = target_path.lower()
            # Scanner checks: file_index[target+".md"] or file_index[target]
            scanner_found = file_index.get(scanner_key_full, []) or file_index.get(target.lower(), [])
            if scanner_found:
                continue  # Scanner considers this valid, skip

            # Also skip if simple filename resolves in index (Obsidian-style)
            simple_found = file_index.get(key, [])
            if simple_found and "/" not in target:
                continue  # Simple [[filename]] that resolves

            candidates = file_index.get(key, [])

            # Strategy 1: RESOLVE — file exists somewhere in vault
            if candidates:
                _, suggested_rel = candidates[0]
                # Try to find best match by parent dir
                target_parts = Path(target).parts
                if len(target_parts) > 1:
                    target_dir = target_parts[-2].lower()
                    for c in candidates:
                        if target_dir in str(c[1]).lower():
                            _, suggested_rel = c
                            break

                display_text = display or Path(target).stem.replace("-", " ").replace("_", " ")
                # Use filename only (Obsidian resolves by filename, not path)
                suggested_name = Path(suggested_rel).stem
                new_link = f"[[{suggested_name}|{display_text}]]"
                new_content = new_content[:m.start()] + new_link + new_content[m.end():]
                file_fixes.append({
                    "target": target,
                    "strategy": "resolve",
                    "resolved_to": suggested_rel,
                })
                continue

            # Strategy 2: TAG-CONVERT — "Layer - X" → tag layer/x
            ns, val = _parse_wikilink_tag(target, tag_patterns)
            if ns:
                tag = f"{ns}/{val}"
                new_content = new_content[:m.start()] + new_content[m.end():]
                # Add tag to frontmatter
                new_content = _add_tag_to_frontmatter(new_content, tag)
                file_fixes.append({
                    "target": target,
                    "strategy": "tag-convert",
                    "tag": tag,
                })
                continue

            # Strategy 3: UNWRAP — remove [[ ]] but keep text visible
            visible_text = display or target
            new_content = new_content[:m.start()] + visible_text + new_content[m.end():]
            file_fixes.append({
                "target": target,
                "strategy": "unwrap",
                "kept_text": visible_text,
            })

        if file_fixes:
            fixes.append({
                "file": rel,
                "action": "fix_wikilinks",
                "count": len(file_fixes),
                "details": file_fixes,
            })
            if not dry_run:
                _write_file(f, new_content)

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
        giri: List of giro numbers to run (default: [0,1,2,3,4,5] = all)

    Returns:
        Dict with results per giro and summary stats.
    """
    if giri is None:
        giri = [0, 1, 2, 3, 4, 5]

    results = {
        "action": "marginalia-fix",
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

    if 5 in giri:
        # Re-build inventory for fresh file index after previous giri
        if not dry_run:
            inventory = giro0_inventory(vault_path)
        g5 = giro5_wikilinks(vault_path, inventory, dry_run=dry_run)
        results["giri"]["5_wikilinks"] = {"fixes": len(g5), "details": g5}
        total_fixes += len(g5)

    results["total_fixes"] = total_fixes
    results["status"] = "applied" if not dry_run else "dry_run"

    return results
