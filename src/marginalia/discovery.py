"""Connection discovery — find links you didn't know you had.

Levi doesn't just fix broken links. It discovers NEW connections
between notes by analyzing:
- Tag affinity: files that share many tags but aren't linked
- Title similarity: files with similar names/topics
- Orphan rescue: orphan files that SHOULD be linked from somewhere
- Cluster bridges: files that could connect separate clusters
"""

import re
from pathlib import Path
from collections import Counter

from .scanner import find_md_files, parse_frontmatter, extract_tags, build_file_index


def _tokenize(text):
    """Simple word tokenizer for similarity."""
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def discover_tag_affinity(vault_path, file_index=None, min_shared_tags=2, max_results=50):
    """Find pairs of files that share tags but aren't linked.

    These are "hidden connections" — notes about the same topic
    that the author never explicitly connected.
    """
    base = Path(vault_path)
    if file_index is None:
        file_index = build_file_index(base)

    # Build tag→files and file→tags maps
    file_tags = {}
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(content)
        if fm:
            rel = str(f.relative_to(base)).replace("\\", "/")
            tags = set(extract_tags(fm))
            if tags:
                file_tags[rel] = tags

    # Build link graph (who links to whom)
    linked_pairs = set()
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(f.relative_to(base)).replace("\\", "/")
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
            target = m.group(2).split("#")[0]
            if not re.match(r"^(https?://|mailto:|#)", target) and target:
                resolved = f.parent / target
                if resolved.exists():
                    try:
                        target_rel = str(resolved.resolve().relative_to(base.resolve())).replace("\\", "/")
                        linked_pairs.add((rel, target_rel))
                        linked_pairs.add((target_rel, rel))
                    except (ValueError, OSError):
                        pass
        # Wikilinks
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content):
            wiki_target = m.group(1).strip()
            key = (wiki_target + ".md").lower() if not wiki_target.endswith(".md") else wiki_target.lower()
            candidates = file_index.get(key, [])
            if candidates:
                target_rel = candidates[0][1]
                linked_pairs.add((rel, target_rel))
                linked_pairs.add((target_rel, rel))

    # Find pairs with shared tags that aren't linked
    suggestions = []
    files = list(file_tags.keys())
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            a, b = files[i], files[j]
            if (a, b) in linked_pairs:
                continue
            shared = file_tags[a] & file_tags[b]
            if len(shared) >= min_shared_tags:
                suggestions.append({
                    "source": a,
                    "target": b,
                    "reason": "tag_affinity",
                    "shared_tags": sorted(shared),
                    "score": len(shared),
                })

    suggestions.sort(key=lambda x: -x["score"])
    return suggestions[:max_results]


def discover_orphan_homes(vault_path, file_index=None, max_results=30):
    """Find where orphan files SHOULD be linked from.

    An orphan is a file nobody links to. We find the best candidate
    "parent" by looking at:
    - Directory siblings (other files in same folder that ARE linked)
    - Tag overlap with hub files
    - Filename similarity with linked files
    """
    base = Path(vault_path)
    if file_index is None:
        file_index = build_file_index(base)

    md_files = find_md_files(base)

    # Build inbound link map
    inbound = {}  # file → set of files that link to it
    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(f.relative_to(base)).replace("\\", "/")
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
            target = m.group(2).split("#")[0]
            if not re.match(r"^(https?://|mailto:|#)", target) and target:
                resolved = f.parent / target
                if resolved.exists():
                    try:
                        target_rel = str(resolved.resolve().relative_to(base.resolve())).replace("\\", "/")
                        inbound.setdefault(target_rel, set()).add(rel)
                    except (ValueError, OSError):
                        pass

    all_files = {str(f.relative_to(base)).replace("\\", "/") for f in md_files}
    orphans = all_files - set(inbound.keys())

    # For each orphan, find best parent candidate
    suggestions = []
    for orphan in orphans:
        orphan_path = Path(orphan)
        orphan_dir = str(orphan_path.parent).replace("\\", "/")
        orphan_tokens = _tokenize(orphan_path.stem)

        # Find siblings in same directory that ARE linked
        siblings_with_links = []
        for linked_file in inbound:
            if str(Path(linked_file).parent).replace("\\", "/") == orphan_dir:
                # Who links to this sibling? Those are candidate parents for our orphan
                for parent in inbound[linked_file]:
                    siblings_with_links.append(parent)

        if siblings_with_links:
            # Most common parent of siblings = best candidate
            parent_counts = Counter(siblings_with_links)
            best_parent, count = parent_counts.most_common(1)[0]
            suggestions.append({
                "orphan": orphan,
                "suggested_parent": best_parent,
                "reason": "sibling_pattern",
                "confidence": min(count / 3.0, 1.0),
                "detail": f"{count} sibling(s) in {orphan_dir} are linked from {best_parent}",
            })
        else:
            # Fallback: find file with most similar name
            best_match, best_score = None, 0
            for other in all_files:
                if other == orphan:
                    continue
                other_tokens = _tokenize(Path(other).stem)
                overlap = len(orphan_tokens & other_tokens)
                if overlap > best_score:
                    best_score = overlap
                    best_match = other
            if best_match and best_score >= 2:
                suggestions.append({
                    "orphan": orphan,
                    "suggested_parent": best_match,
                    "reason": "name_similarity",
                    "confidence": min(best_score / 4.0, 1.0),
                    "detail": f"Similar filename tokens with {best_match}",
                })

    suggestions.sort(key=lambda x: -x.get("confidence", 0))
    return suggestions[:max_results]


def discover_cluster_bridges(vault_path, file_index=None, max_results=20):
    """Find files that could bridge separate tag clusters.

    A "bridge" is a file tagged with topics from 2+ different clusters,
    making it a natural connector between knowledge areas.
    Useful for building MOCs (Maps of Content) in Obsidian.
    """
    base = Path(vault_path)
    if file_index is None:
        file_index = build_file_index(base)

    # Build file→tags with namespace prefix as cluster key
    file_clusters = {}
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(content)
        if fm:
            rel = str(f.relative_to(base)).replace("\\", "/")
            tags = extract_tags(fm)
            clusters = set()
            for tag in tags:
                if "/" in tag:
                    ns, value = tag.split("/", 1)
                    if ns in ("domain", "artifact", "process", "tech", "course", "type"):
                        clusters.add(f"{ns}/{value}")
                else:
                    clusters.add(tag)
            if len(clusters) >= 2:
                file_clusters[rel] = sorted(clusters)

    # Files spanning 3+ distinct domain-level topics are the best bridges
    bridges = []
    for rel, clusters in file_clusters.items():
        # Count distinct top-level domains
        domains = set()
        for c in clusters:
            if "/" in c:
                domains.add(c.split("/")[1])
            else:
                domains.add(c)
        if len(domains) >= 3:
            bridges.append({
                "file": rel,
                "reason": "multi_domain_bridge",
                "domains": sorted(domains),
                "domain_count": len(domains),
                "tags": clusters,
            })

    bridges.sort(key=lambda x: -x["domain_count"])
    return bridges[:max_results]


def discover_all(vault_path, min_shared_tags=2, max_results=50):
    """Run all discovery algorithms and return combined suggestions."""
    file_index = build_file_index(Path(vault_path))

    tag_connections = discover_tag_affinity(vault_path, file_index, min_shared_tags, max_results)
    orphan_homes = discover_orphan_homes(vault_path, file_index, max_results=30)
    bridges = discover_cluster_bridges(vault_path, file_index, max_results=20)

    return {
        "tag_affinity": {
            "description": "Files sharing tags but not linked — hidden connections",
            "count": len(tag_connections),
            "suggestions": tag_connections,
        },
        "orphan_homes": {
            "description": "Where orphan files should be linked from",
            "count": len(orphan_homes),
            "suggestions": orphan_homes,
        },
        "cluster_bridges": {
            "description": "Files bridging multiple knowledge domains — ideal for MOCs",
            "count": len(bridges),
            "suggestions": bridges,
        },
    }
