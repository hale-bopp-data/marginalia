"""Tag taxonomy engine — configurable namespaces, migration, classification."""

import re
from pathlib import Path

# Default taxonomy (EasyWay-derived, good starting point for any vault)
DEFAULT_NAMESPACES = {
    "domain": {"notes", "research", "project", "course", "thesis", "lecture", "reading"},
    "artifact": {"documentation", "guide", "template", "readme", "checklist", "faq", "report",
                 "essay", "paper", "abstract", "bibliography", "annotation", "summary", "review"},
    "process": {"planning", "review", "workflow", "methodology", "sprint", "backlog", "roadmap",
                "brainstorm", "outline", "draft", "revision", "final"},
    "tech": {"python", "bash", "javascript", "typescript", "sql", "yaml", "json", "latex",
             "html", "css", "r", "matlab", "jupyter"},
    "meta": {"index", "archive", "wip", "stub", "deprecated"},
}

DEFAULT_MERGES = {
    "docs": "documentation",
    "doc": "documentation",
    "todo": "planning",
    "todos": "planning",
}

DEFAULT_CASE_FIXES = {
    "DOMAIN/": "domain/",
    "ARTIFACT/": "artifact/",
    "PROCESS/": "process/",
    "TECH/": "tech/",
    "META/": "meta/",
}


def load_taxonomy(yaml_path=None):
    """Load taxonomy from a YAML file, or return defaults.

    Expected YAML format:
        namespaces:
          domain: [notes, research, project]
          artifact: [documentation, guide]
        merges:
          docs: documentation
        case_fixes:
          DOMAIN/: domain/
    """
    if yaml_path is None:
        return DEFAULT_NAMESPACES, DEFAULT_MERGES, DEFAULT_CASE_FIXES

    path = Path(yaml_path)
    if not path.exists():
        return DEFAULT_NAMESPACES, DEFAULT_MERGES, DEFAULT_CASE_FIXES

    # Simple YAML parsing without pyyaml dependency
    # When a YAML taxonomy exists, it IS the source of truth (G16).
    # Defaults are only for when no YAML is provided.
    content = path.read_text(encoding="utf-8")
    namespaces = {}
    merges = {}
    case_fixes = {}

    current_section = None
    current_ns = None
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped == "namespaces:":
            current_section = "namespaces"
            continue
        elif stripped == "merges:":
            current_section = "merges"
            continue
        elif stripped == "case_fixes:":
            current_section = "case_fixes"
            continue

        if current_section == "namespaces":
            m = re.match(r"(\w+):\s*\[(.+)\]", stripped)
            if m:
                ns_name = m.group(1)
                tags = {t.strip().strip("'\"") for t in m.group(2).split(",") if t.strip()}
                namespaces[ns_name] = tags
                continue
            m = re.match(r"(\w+):", stripped)
            if m and not stripped.endswith("]"):
                current_ns = m.group(1)
                if current_ns not in namespaces:
                    namespaces[current_ns] = set()
                continue
            if current_ns and stripped.startswith("- "):
                tag = stripped[2:].strip().strip("'\"")
                namespaces.setdefault(current_ns, set()).add(tag)
                continue

        elif current_section == "merges":
            m = re.match(r"(\S+):\s*(\S+)", stripped)
            if m:
                merges[m.group(1)] = m.group(2)

        elif current_section == "case_fixes":
            m = re.match(r"(\S+):\s*(\S+)", stripped)
            if m:
                case_fixes[m.group(1)] = m.group(2)

    return namespaces, merges, case_fixes


def validate_taxonomy(yaml_path):
    """Validate a taxonomy YAML file. Returns list of issues (empty = valid)."""
    issues = []
    path = Path(yaml_path)
    if not path.exists():
        return [{"type": "file_not_found", "detail": f"Taxonomy file not found: {yaml_path}"}]

    namespaces, merges, case_fixes = load_taxonomy(yaml_path)

    # Check: merge targets must exist as namespace values
    all_values = set()
    for ns, vals in namespaces.items():
        all_values.update(vals)

    for alias, target in merges.items():
        if target not in all_values:
            issues.append({
                "type": "orphan_merge",
                "detail": f"merge '{alias}: {target}' — target '{target}' not in any namespace",
            })

    # Check: no duplicate values across namespaces (except intentional)
    seen_values = {}  # value → [namespaces]
    for ns, vals in namespaces.items():
        for v in vals:
            seen_values.setdefault(v, []).append(ns)
    for v, nss in seen_values.items():
        if len(nss) > 1:
            issues.append({
                "type": "duplicate_value",
                "detail": f"value '{v}' appears in multiple namespaces: {', '.join(nss)}",
            })

    # Check: merge alias should not be a canonical namespace value
    for alias in merges:
        if alias in all_values and merges[alias] != alias:
            issues.append({
                "type": "alias_shadows_canonical",
                "detail": f"merge alias '{alias}' is also a canonical value — ambiguous",
            })

    return issues


def migrate_tag(tag, namespaces=None, merges=None, case_fixes=None):
    """Apply migration rules to a single tag. Returns new tag or None if unchanged."""
    if namespaces is None:
        namespaces = DEFAULT_NAMESPACES
    if merges is None:
        merges = DEFAULT_MERGES
    if case_fixes is None:
        case_fixes = DEFAULT_CASE_FIXES

    original = tag

    # Step 1: Case fixes (e.g. DOMAIN/foo -> domain/foo)
    for old_prefix, new_prefix in case_fixes.items():
        if tag.startswith(old_prefix):
            tag = new_prefix + tag[len(old_prefix):].lower()
            break

    # Already namespaced and changed? Return it
    if "/" in tag and tag != original:
        return tag
    # Already namespaced and unchanged? Skip
    if "/" in tag:
        return None

    # Step 2: Merge synonyms
    t_lower = tag.lower()
    if t_lower in merges:
        tag = merges[t_lower]
        t_lower = tag.lower()

    # Step 3: Classify into namespace
    for ns_name, ns_tags in namespaces.items():
        if t_lower in ns_tags:
            return f"{ns_name}/{t_lower}"

    # Step 4: Heuristic patterns
    if t_lower.startswith("artifact-"):
        return f"artifact/{t_lower}"

    return tag if tag != original else None


def fix_tags_in_file(filepath, dry_run=True, namespaces=None, merges=None, case_fixes=None):
    """Migrate tags in a single file's frontmatter. Returns changes dict or None."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not fm_match:
        return None

    fm_text = fm_match.group(2)
    tags_match = re.search(r"^(tags:\s*)\[([^\]]*)\]", fm_text, re.MULTILINE)
    if not tags_match:
        return None

    old_tags = [t.strip().strip("'\"") for t in tags_match.group(2).split(",") if t.strip()]
    if not old_tags:
        return None

    new_tags, changed = [], False
    for tag in old_tags:
        new_tag = migrate_tag(tag, namespaces, merges, case_fixes)
        if new_tag is not None:
            new_tags.append(new_tag)
            changed = True
        else:
            new_tags.append(tag)

    if not changed:
        return None

    # Deduplicate
    seen, deduped = set(), []
    for t in new_tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            deduped.append(t)

    if not dry_run:
        new_line = f"{tags_match.group(1)}[{', '.join(deduped)}]"
        new_content = content.replace(tags_match.group(0), new_line, 1)
        filepath.write_text(new_content, encoding="utf-8")

    return {old: new for old, new in zip(old_tags, new_tags) if old != new}
