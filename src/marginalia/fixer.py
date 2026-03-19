"""Multi-pass vault fixer — the 7 Giri (rounds) with actual repairs.

Giro 0: Inventory — build file index, parse all frontmatter, snapshot state
Giro 1: Frontmatter — add missing frontmatter, fill required fields
Giro 2: Tags — migrate flat→namespaced, merge duplicates, case normalize
Giro 3: Links — fix stale markdown links using file index suggestions
Giro 4: Obsidian — fix .gitignore, remove Untitled.canvas
Giro 5: Wikilinks — resolve, tag-convert, or unwrap broken [[wikilinks]]
Giro 6: Domain Tags — assign domain/ tags via taxonomy merge or path inference
Giro 7: Frontmatter Quality — fix stale drafts, placeholder summaries, empty fields

Each giro reads the state left by the previous one (no stale data).
"""

import os
import re
import json
from pathlib import Path
from datetime import date, datetime, timezone

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


# --- Giro 6: Domain Tags ---

# Path → domain inference map. Directory patterns that strongly imply a domain.
# Only used when no domain/ tag exists AND no taxonomy merge resolves.
_PATH_DOMAIN_MAP = {
    "argos": "datalake",
    "hale-bopp": "architecture",
    "hale-bopp/db-hale-bopp": "db",
    "hale-bopp/etl-hale-bopp": "datalake",
    "Runbooks": "infra",
    "security": "security",
    "standards": "docs",
    "chronicles": "docs",
    "indices": "docs",
    "planning": "platform",
    "architecture": "architecture",
    "concept": "architecture",
    "deployment": "infra",
    "etl": "datalake",
    "UX": "frontend",
    "governance": "docs",
    "vision": "architecture",
    "logs": "platform",
    "Patterns": "docs",
    "best-practices": "docs",
    "control-plane": "agents",
    "prompts": "agents",
    "tools": "platform",
    "use-cases": "docs",
}


def giro6_domain_tags(vault_path, inventory, taxonomy_path=None, dry_run=True):
    """Assign domain/ tags to files that don't have one.

    Three strategies in confidence order:
    1. TAXONOMY — file has a flat tag that is a domain alias in taxonomy merges
       e.g. flat tag "argos" + merge "argos: datalake" → add domain/datalake
    2. PATH — directory strongly implies a domain (e.g. security/ → domain/security)
    3. SKIP — not inferrable, reported for manual review or brain analysis

    Only touches files with frontmatter + tags but no domain/ tag.
    Skips templates and archive.
    """
    base = Path(vault_path)
    fixes = []
    skipped = []

    # Load taxonomy merges for strategy 1
    domain_merges = {}  # flat value → canonical domain
    domain_values = set()
    if taxonomy_path:
        namespaces, merges, _ = load_taxonomy(taxonomy_path)
        domain_values = namespaces.get("domain", set())
        # Merges that resolve to a domain value
        for alias, target in merges.items():
            if target in domain_values:
                domain_merges[alias] = target

    for f in inventory["md_files"]:
        rel = str(f.relative_to(base)).replace("\\", "/")

        # Skip template/archive
        if "template" in rel.lower() or "/archive/" in rel or rel.startswith("archive/"):
            continue

        content = _read_file(f)
        if content is None:
            continue

        fm = parse_frontmatter(content)
        if fm is None:
            continue

        tags = extract_tags(fm)
        if any(t.startswith("domain/") for t in tags):
            continue  # already has domain tag

        # Strategy 1: TAXONOMY — check if any existing flat tag resolves to a domain
        resolved_domain = None
        resolved_via = None
        for tag in tags:
            # Strip namespace if present (e.g. process/datalake → datalake)
            val = tag.split("/")[-1] if "/" in tag else tag
            if val in domain_merges:
                resolved_domain = domain_merges[val]
                resolved_via = f"taxonomy merge: {val} -> {resolved_domain}"
                break
            # Direct match on domain values
            if val in domain_values:
                resolved_domain = val
                resolved_via = f"taxonomy direct: {val}"
                break

        # Strategy 2: PATH — directory implies domain
        if not resolved_domain:
            norm_path = rel.replace("\\", "/")
            # Try longest prefix first (hale-bopp/db-hale-bopp before hale-bopp)
            for prefix in sorted(_PATH_DOMAIN_MAP.keys(), key=len, reverse=True):
                if norm_path.startswith(prefix + "/") or norm_path.startswith(prefix.lower() + "/"):
                    resolved_domain = _PATH_DOMAIN_MAP[prefix]
                    resolved_via = f"path inference: {prefix}/ -> {resolved_domain}"
                    break

        # Strategy 3: SKIP
        if not resolved_domain:
            skipped.append({
                "file": rel,
                "existing_tags": tags[:5],
                "reason": "no taxonomy merge or path pattern matched",
            })
            continue

        # Apply: add domain/X to frontmatter
        domain_tag = f"domain/{resolved_domain}"
        new_content = _add_tag_to_frontmatter(content, domain_tag)

        if new_content != content:
            fixes.append({
                "file": rel,
                "action": "add_domain_tag",
                "domain_tag": domain_tag,
                "strategy": resolved_via,
                "existing_tags": tags[:5],
            })
            if not dry_run:
                _write_file(f, new_content)

    return {"fixes": fixes, "skipped": skipped}


# --- Giro 7: Frontmatter Quality (S156) ---

# Stale draft classification rules — path prefix → new status.
# Order: most specific first. First match wins.
_STALE_DRAFT_RULES = [
    # Auto-generated index pages are functional, not drafts
    {"path_prefix": "indices/",      "new_status": "active",     "reason": "auto-generated MOC index"},
    # Legacy webapp docs — schema/code from old portal
    {"path_prefix": "easyway-webapp/", "new_status": "deprecated", "reason": "legacy portal schema"},
    # Legacy orchestrations superseded by n8n/marginalia
    {"path_prefix": "orchestrations/", "new_status": "deprecated", "reason": "legacy orchestration"},
    # Old concepts/architecture — fossils
    {"path_prefix": "concept/",      "new_status": "deprecated", "reason": "legacy concept doc"},
    # Old directory
    {"path_prefix": "old/",          "new_status": "deprecated", "reason": "archived content"},
    # Log reports are snapshots, not drafts
    {"path_prefix": "logs/",         "new_status": "deprecated", "reason": "historical log report"},
]

# Age threshold: drafts older than this (days) with no matching rule get promoted
_STALE_DRAFT_MAX_AGE_DAYS = 30

# Placeholder patterns for summary field
_SUMMARY_PLACEHOLDERS = {"todo", "tbd", "fixme", "xxx", "...", "placeholder",
                         "da completare", "da fare", ">"}


def _extract_first_sentence(content):
    """Extract first meaningful sentence from markdown body (after frontmatter)."""
    # Strip frontmatter
    stripped = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", content, count=1, flags=re.DOTALL)
    for line in stripped.split("\n"):
        line = line.strip()
        # Skip headings, empty lines, images, html, lists starting with -
        if not line or line.startswith("#") or line.startswith("!") or line.startswith("<"):
            continue
        if line.startswith("-") or line.startswith("|") or line.startswith(">"):
            continue
        if line.startswith("```"):
            break
        # Clean markdown inline formatting
        clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)  # [text](url) → text
        clean = re.sub(r"[*_`]", "", clean).strip()
        if len(clean) >= 10:
            # Truncate to ~120 chars at word boundary
            if len(clean) > 120:
                clean = clean[:117].rsplit(" ", 1)[0] + "..."
            return clean
    return None


def _title_to_summary(title):
    """Generate a minimal summary from the title when no body content is available."""
    clean = title.strip().strip("'\"")
    if len(clean) >= 10:
        return clean
    return None


def _set_fm_field(content, field, value):
    """Set a frontmatter field value. Adds if missing, replaces if exists.

    Handles YAML block scalars (> and |): when the existing value is a block
    scalar indicator, the continuation lines (indented) are also removed before
    inserting the new inline value.
    """
    # Match the frontmatter block
    fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not fm_match:
        return content

    fm_text = fm_match.group(2)

    # Check if field already exists
    field_pattern = re.compile(r"^(" + re.escape(field) + r"\s*:\s*)(.*)$", re.MULTILINE)
    field_m = field_pattern.search(fm_text)

    if field_m:
        old_val = field_m.group(2).strip()
        end_pos = field_m.end(2)

        is_block_scalar = old_val in (">", "|", ">-", "|-")

        # If old value is a YAML block scalar indicator (> or |), consume continuation lines
        if is_block_scalar:
            rest = fm_text[end_pos:]
            lines = rest.split("\n")
            consumed = 0
            for ln in lines:
                if ln == "":
                    consumed += 1
                elif ln.startswith("  ") or ln.startswith("\t"):
                    consumed += 1
                else:
                    break
            # Rejoin remaining lines (the next field onward)
            remaining = "\n".join(lines[consumed:])
            new_fm = fm_text[:field_m.start(2)] + value + "\n" + remaining
        else:
            new_fm = fm_text[:field_m.start(2)] + value + fm_text[end_pos:]
    else:
        # Append new field
        new_fm = fm_text + f"\n{field}: {value}"

    return content[:fm_match.start(2)] + new_fm + content[fm_match.end(2):]


def giro7_frontmatter_quality(vault_path, inventory, dry_run=True):
    """Fix frontmatter quality issues: stale drafts, placeholder summaries, empty fields.

    Three sub-passes:
    A. Stale drafts → promote to active or deprecate (rule-based)
    B. Placeholder summaries → generate from body content or title
    C. Empty required fields → fill from filename/content

    Returns dict with fixes list and stats.
    """
    base = Path(vault_path)
    fixes = []
    stats = {"stale_draft_fixed": 0, "summary_fixed": 0, "empty_field_fixed": 0}

    for f in inventory["md_files"]:
        rel = str(f.relative_to(base)).replace("\\", "/")
        content = _read_file(f)
        if content is None:
            continue

        fm = parse_frontmatter(content)
        if fm is None:
            continue

        modified = False
        new_content = content

        # --- A. Stale draft resolution ---
        status_val = fm.get("status", "").strip().strip("'\"").lower()
        updated_val = fm.get("updated", "").strip().strip("'\"")

        if status_val == "draft" and updated_val:
            try:
                updated_date = date.fromisoformat(updated_val)
                days_stale = (date.today() - updated_date).days
            except (ValueError, TypeError):
                days_stale = 0

            if days_stale > _STALE_DRAFT_MAX_AGE_DAYS:
                # Apply rules — first match wins
                new_status = "active"  # default: promote
                reason = f"draft stale {days_stale}d, promoted"
                for rule in _STALE_DRAFT_RULES:
                    if rel.startswith(rule["path_prefix"]):
                        new_status = rule["new_status"]
                        reason = rule["reason"]
                        break
                else:
                    # No path rule matched — check extreme age
                    if days_stale > 365:
                        new_status = "deprecated"
                        reason = f"fossil ({days_stale}d without update)"

                new_content = _set_fm_field(new_content, "status", new_status)
                fixes.append({
                    "file": rel, "action": "stale_draft_resolve",
                    "old_status": "draft", "new_status": new_status,
                    "days_stale": days_stale, "reason": reason,
                })
                stats["stale_draft_fixed"] += 1
                modified = True

        # --- B. Placeholder summary fix ---
        summary_val = fm.get("summary", "")
        if summary_val:
            summary_clean = summary_val.strip().strip("'\"")
            if summary_clean.lower() in _SUMMARY_PLACEHOLDERS or len(summary_clean) < 10:
                # Try to generate from body
                generated = _extract_first_sentence(content)
                if not generated:
                    # Fallback: derive from title
                    generated = _title_to_summary(fm.get("title", ""))
                if generated:
                    new_content = _set_fm_field(new_content, "summary", f"'{generated}'")
                    fixes.append({
                        "file": rel, "action": "summary_generate",
                        "old_summary": summary_clean, "new_summary": generated,
                    })
                    stats["summary_fixed"] += 1
                    modified = True

        # --- C. Empty required fields ---
        for field in ("id", "title", "summary"):
            val = fm.get(field, None)
            if val is not None:
                clean = val.strip().strip("'\"").strip()
                if not clean:
                    if field == "id":
                        generated = f.stem.lower().replace(" ", "-")
                    elif field == "title":
                        generated = f.stem.replace("-", " ").replace("_", " ").title()
                    else:  # summary
                        generated = _extract_first_sentence(content)
                        if not generated:
                            generated = _title_to_summary(fm.get("title", f.stem))
                    if generated:
                        quote = "'" if field == "summary" else ""
                        new_content = _set_fm_field(new_content, field, f"{quote}{generated}{quote}")
                        fixes.append({
                            "file": rel, "action": f"fill_empty_{field}",
                            "generated": generated,
                        })
                        stats["empty_field_fixed"] += 1
                        modified = True

        # --- D. Stamp updated date on modified files ---
        if modified:
            today_iso = date.today().isoformat()
            new_content = _set_fm_field(new_content, "updated", f"'{today_iso}'")

        if modified and not dry_run:
            _write_file(f, new_content)

    return {"fixes": fixes, "stats": stats}


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
        giri = [0, 1, 2, 3, 4, 5, 6, 7]

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

    if 6 in giri:
        # Re-build inventory after tag changes from giro 2
        if not dry_run:
            inventory = giro0_inventory(vault_path)
        g6 = giro6_domain_tags(vault_path, inventory, taxonomy_path=taxonomy_path, dry_run=dry_run)
        results["giri"]["6_domain_tags"] = {
            "fixes": len(g6["fixes"]),
            "skipped": len(g6["skipped"]),
            "details": g6["fixes"],
            "needs_review": g6["skipped"],
        }
        total_fixes += len(g6["fixes"])

    if 7 in giri:
        if not dry_run:
            inventory = giro0_inventory(vault_path)
        g7 = giro7_frontmatter_quality(vault_path, inventory, dry_run=dry_run)
        results["giri"]["7_frontmatter_quality"] = {
            "fixes": len(g7["fixes"]),
            "stats": g7["stats"],
            "details": g7["fixes"],
        }
        total_fixes += len(g7["fixes"])

    results["total_fixes"] = total_fixes
    results["status"] = "applied" if not dry_run else "dry_run"

    return results
