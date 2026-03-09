"""Core scanning engine — frontmatter, empty sections, broken links, graph."""

import os
import re
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".obsidian", "__pycache__", "dist", "build", ".cache", ".trash"}
SKIP_FILES = {"CHANGELOG.md", "LICENSE.md"}

FM_REQUIRED_DEFAULT = ["title", "tags"]


def find_md_files(vault_path, skip_dirs=None, skip_files=None):
    """
    Find all .md files in one or more vault paths, respecting skip rules.

    vault_path may be a single path (str/Path) or a list of paths.
    Duplicate files (same resolved path) are deduplicated.
    """
    skip_d = skip_dirs or SKIP_DIRS
    skip_f = skip_files or SKIP_FILES

    # Normalise to list
    if isinstance(vault_path, (str, Path)):
        paths = [Path(vault_path)]
    else:
        paths = [Path(p) for p in vault_path]

    seen = set()
    files = []
    for root_path in paths:
        for root, dirs, filenames in os.walk(root_path):
            dirs[:] = [d for d in dirs if d not in skip_d]
            for f in filenames:
                if f.endswith(".md") and f not in skip_f:
                    fp = Path(root) / f
                    resolved = fp.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(fp)
    return files


def parse_frontmatter(content):
    """Extract YAML frontmatter fields (no yaml dependency)."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    fm_text = match.group(1)
    fields = {}
    for line in fm_text.split("\n"):
        m = re.match(r"^(\w[\w.-]*)\s*:\s*(.+)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def extract_tags(fm_fields):
    """Extract tags list from frontmatter."""
    tags_raw = fm_fields.get("tags", "")
    match = re.search(r"\[([^\]]*)\]", tags_raw)
    if match:
        return [t.strip().strip("'\"") for t in match.group(1).split(",") if t.strip()]
    return []


def build_file_index(vault_path):
    """Build filename.lower() -> [(abs_path, rel_path)] index for link resolution."""
    index = {}
    base = Path(vault_path)
    for f in find_md_files(base):
        key = f.name.lower()
        rel = str(f.relative_to(base)).replace("\\", "/")
        index.setdefault(key, []).append((f, rel))
    return index


def suggest_correct_path(filepath, target_filename, file_index):
    """Find where a broken link target actually lives."""
    key = Path(target_filename).name.lower()
    if not key.endswith(".md"):
        key += ".md"
    candidates = file_index.get(key, [])
    if not candidates:
        return None, None

    best = candidates[0]
    target_parts = Path(target_filename).parts
    if len(target_parts) > 1:
        target_dir = target_parts[-2].lower()
        for c in candidates:
            if target_dir in str(c[1]).lower():
                best = c
                break

    try:
        rel = os.path.relpath(best[0], filepath.parent).replace("\\", "/")
    except ValueError:
        rel = best[1]
    return rel, best[1]


def scan_file(filepath, vault_path, file_index=None, required_fields=None):
    """Scan a single .md file for quality issues."""
    issues = []
    required_fields = required_fields or FM_REQUIRED_DEFAULT

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [{"file": str(filepath), "type": "read_error", "line": 0,
                 "description": f"Cannot read: {e}", "auto_fixable": False}]

    rel_path = str(filepath.relative_to(vault_path)).replace("\\", "/")

    # 1. Frontmatter check
    fm = parse_frontmatter(content)
    if fm is None:
        issues.append({
            "file": rel_path, "type": "missing_frontmatter", "line": 1,
            "description": "No YAML frontmatter found",
            "auto_fixable": False,
        })
    else:
        for field in required_fields:
            if field not in fm:
                issues.append({
                    "file": rel_path, "type": "incomplete_frontmatter", "line": 1,
                    "description": f"Missing frontmatter field: {field}",
                    "auto_fixable": False,
                })

    # 2. Empty sections
    lines = content.split("\n")
    for i, line in enumerate(lines[:-1]):
        if re.match(r"^#{1,3} ", line):
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            after_next = lines[i + 2].strip() if i + 2 < len(lines) else ""
            if next_line == "" and (i + 2 >= len(lines) or re.match(r"^#{1,3} ", after_next)):
                issues.append({
                    "file": rel_path, "type": "empty_section", "line": i + 1,
                    "description": f"Empty section: {line.strip()}",
                    "auto_fixable": False,
                })

    # 3. Broken markdown links [text](target)
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
        link_text, link_target = m.group(1), m.group(2)
        if re.match(r"^(https?://|mailto:|#)", link_target):
            continue
        link_path = link_target.split("#")[0]
        if not link_path:
            continue
        line_num = content[:m.start()].count("\n") + 1
        resolved = filepath.parent / link_path
        if not resolved.exists():
            if file_index:
                suggested_rel, suggested_full = suggest_correct_path(filepath, link_path, file_index)
            else:
                suggested_rel, suggested_full = None, None
            if suggested_rel:
                issues.append({
                    "file": rel_path, "type": "stale_link", "line": line_num,
                    "description": f"Stale link: [{link_text}]({link_target})",
                    "fix": f"Replace with: [{link_text}]({suggested_rel})",
                    "auto_fixable": True,
                })
            else:
                issues.append({
                    "file": rel_path, "type": "broken_link", "line": line_num,
                    "description": f"Broken link: [{link_text}]({link_target})",
                    "auto_fixable": False,
                })

    # 4. Broken wikilinks [[target]] or [[target|display]]
    for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content):
        wiki_target = m.group(1).strip()
        line_num = content[:m.start()].count("\n") + 1
        if file_index:
            key = (wiki_target + ".md").lower() if not wiki_target.endswith(".md") else wiki_target.lower()
            candidates = file_index.get(key, [])
            if not candidates:
                # Try without .md extension for exact filename match
                candidates = file_index.get(wiki_target.lower(), [])
            if not candidates:
                issues.append({
                    "file": rel_path, "type": "broken_wikilink", "line": line_num,
                    "description": f"Broken wikilink: [[{wiki_target}]]",
                    "auto_fixable": False,
                })

    return issues


def build_graph(vault_path, file_index=None):
    """Build the relationship graph: tags, links, topology, clusters."""
    base = Path(vault_path)
    if file_index is None:
        file_index = build_file_index(base)

    all_files_fm = {}
    md_files = find_md_files(base)

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(content)
            if fm:
                rel = str(f.relative_to(base)).replace("\\", "/")
                all_files_fm[rel] = fm
        except Exception:
            pass

    # Tag index
    tag_index = {}
    for rel_path, fm in all_files_fm.items():
        for tag in extract_tags(fm):
            tag_index.setdefault(tag, []).append(rel_path)

    # Link graph
    link_graph = {}
    all_linked_targets = set()

    for filepath in md_files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel_path = str(filepath.relative_to(base)).replace("\\", "/")
        outgoing = []

        # Markdown links
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
            link_target = m.group(2)
            if re.match(r"^(https?://|mailto:|#)", link_target):
                continue
            link_path = link_target.split("#")[0]
            if not link_path:
                continue
            resolved = filepath.parent / link_path
            if resolved.exists():
                try:
                    target_rel = str(resolved.resolve().relative_to(base.resolve())).replace("\\", "/")
                    outgoing.append(target_rel)
                    all_linked_targets.add(target_rel)
                except (ValueError, OSError):
                    pass

        # Wikilinks
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content):
            wiki_target = m.group(1).strip()
            key = (wiki_target + ".md").lower() if not wiki_target.endswith(".md") else wiki_target.lower()
            candidates = file_index.get(key, [])
            if candidates:
                outgoing.append(candidates[0][1])
                all_linked_targets.add(candidates[0][1])

        if outgoing:
            link_graph[rel_path] = sorted(set(outgoing))

    all_files = {str(f.relative_to(base)).replace("\\", "/") for f in md_files}
    orphans = sorted(all_files - all_linked_targets)

    # Tag quality
    namespaced_count = sum(1 for t in tag_index if "/" in t)
    flat_count = sum(1 for t in tag_index if "/" not in t)

    # Topology
    inbound = {}
    for targets in link_graph.values():
        for t in targets:
            inbound[t] = inbound.get(t, 0) + 1

    hubs = sorted(
        [(f, len(targets)) for f, targets in link_graph.items()],
        key=lambda x: -x[1]
    )[:20]
    authorities = sorted(inbound.items(), key=lambda x: -x[1])[:20]

    # Clusters by primary domain tag
    clusters = {}
    for rel_path, fm in all_files_fm.items():
        tags = extract_tags(fm)
        domain_tags = [t for t in tags if "/" in t]
        cluster_key = domain_tags[0].split("/")[0] if domain_tags else "_untagged"
        clusters.setdefault(cluster_key, []).append(rel_path)

    return {
        "tag_index": {t: sorted(files) for t, files in sorted(tag_index.items())},
        "tag_count": len(tag_index),
        "namespaced_tags": namespaced_count,
        "flat_tags": flat_count,
        "link_graph": link_graph,
        "link_count": sum(len(v) for v in link_graph.values()),
        "topology": {
            "hubs": [{"file": f, "outgoing": c} for f, c in hubs],
            "authorities": [{"file": f, "inbound": c} for f, c in authorities],
            "avg_outgoing": round(sum(len(v) for v in link_graph.values()) / max(len(link_graph), 1), 1),
            "avg_inbound": round(sum(inbound.values()) / max(len(inbound), 1), 1),
            "files_with_links": len(link_graph),
            "files_linked_to": len(inbound),
        },
        "clusters": {k: len(v) for k, v in sorted(clusters.items(), key=lambda x: -len(x[1]))},
        "orphans": orphans,
        "orphan_count": len(orphans),
    }
