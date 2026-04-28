"""Generic layer classification engine — taxonomy-driven, zero dependencies.

Classifies vault files into layers (e.g. L0/L1/L2/L3) using a cascade of methods:
  1. Explicit tag  (e.g. layer/L1 in frontmatter)
  2. Path pattern  (glob/regex matching directory patterns)
  3. Content heuristics (pointer density, heading depth, line count)
  4. Type-based    (frontmatter 'type' field)
  5. LLM fallback  (via brain.py, optional)

The layer definitions come from a taxonomy YAML file — NOT hardcoded.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .scanner import find_md_files, parse_frontmatter, extract_tags

# ---------------------------------------------------------------------------
# Taxonomy helpers
# ---------------------------------------------------------------------------

def _parse_yaml_shallow(text: str) -> dict:
    """Parse a shallow YAML file (two levels deep: layers > L0 > rules > pattern)."""
    result = {}
    current = result
    stack = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        stripped = line.strip()

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)", stripped)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip()

        # Pop stack to match indent level
        while stack and stack[-1][0] >= indent:
            _, _, current = stack.pop()

        if val:
            # Handle inline list: [a, b, c]
            if val.startswith("["):
                inner = val.strip("[]")
                current[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            else:
                current[key] = val.strip().strip("'\"")
        else:
            # New block — push current
            stack.append((indent, key, current))
            child = {}
            current[key] = child
            current = child

    return result


def load_taxonomy(taxonomy_path: str | Path) -> dict:
    """Load a taxonomy YAML file defining layers and rules.

    Expected format:

        layers:
          L0:
            label: Shell
            description: Bootstrap navigation
            rules:
              pattern: "**/_index.md"
              max_lines: 80
              min_pointer_density: 0.6
              type: index
          L1:
            label: Arms
            rules:
              pattern: "guides/*quickstart*"
              max_lines: 200
              type: [quickstart, runbook]
    """
    path = Path(taxonomy_path)
    if not path.is_file():
        raise FileNotFoundError(f"Taxonomy file not found: {taxonomy_path}")

    raw = _parse_yaml_shallow(path.read_text(encoding="utf-8", errors="replace"))
    layers = raw.get("layers", {})

    # Validate: each layer must have rules
    valid = {}
    for name, spec in layers.items():
        if not isinstance(spec, dict):
            continue
        rules = spec.get("rules", {})
        if not isinstance(rules, dict):
            # List of rule blocks
            if isinstance(rules, list):
                rules = {}
            else:
                continue
        valid[name] = {"label": spec.get("label", name), "rules": rules}
        if isinstance(spec.get("description"), str):
            valid[name]["description"] = spec["description"]

    return valid


# ---------------------------------------------------------------------------
# Classification engine
# ---------------------------------------------------------------------------

def _match_pattern(pattern: str, rel_path: str) -> bool:
    """Simple glob+regex pattern matching for file paths.

    Supports:
      - **/foo.md       — matches anywhere
      - guides/*.md     — single-level glob
      - guides/**        — recursive glob
      - /regex/          — if pattern starts and ends with /, treat as regex
    """
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        # Regex mode: /regex/
        return bool(re.search(pattern[1:-1], rel_path))

    # Glob mode
    escaped = re.escape(pattern)
    glob_re = escaped.replace(r"\*\*", "___RECURSIVE___").replace(r"\*", "[^/]*").replace("___RECURSIVE___", ".*")
    glob_re = "^" + glob_re + "$"
    return bool(re.match(glob_re, rel_path))


def _pointer_density(content: str, rel_path: str) -> float:
    """Ratio of link lines to total lines — high density suggests navigational content."""
    lines = content.split("\n")
    if not lines:
        return 0.0
    link_lines = sum(1 for ln in lines if re.search(r"\[\[.+?\]\]|\[.+?\]\(.+?\)", ln))
    return link_lines / len(lines)


def _max_heading_depth(content: str) -> int:
    """Maximum heading level (h1=1, h2=2, ...) in the document."""
    depths = set()
    for m in re.finditer(r"^(#{1,6}) ", content, re.MULTILINE):
        depths.add(len(m.group(1)))
    return max(depths) if depths else 0


def _count_lines(content: str) -> int:
    return len(content.split("\n"))


def classify_file(
    rel_path: str,
    content: str,
    fm: dict | None,
    layers: dict,
) -> dict | None:
    """Classify a single file into one of the defined layers.

    Returns: {layer, confidence, method, rationale} or None if unclassifiable.
    Priority cascade: tag > path > content > type > (LLM — caller responsibility).
    """
    if not layers:
        return None

    tags = extract_tags(fm) if fm else []
    line_count = _count_lines(content)
    p_density = _pointer_density(content, rel_path)
    h_depth = _max_heading_depth(content)
    fm_type = fm.get("type", "").strip().strip("'\"") if fm else ""

    best = None
    best_confidence = -1

    for name, spec in layers.items():
        rules = spec.get("rules", {})
        if not isinstance(rules, dict):
            continue

        probes = []

        # Method 1: explicit tag
        tag_key = f"layer/{name}"
        if tag_key in tags:
            probes.append((0.98, "tag", f"Explicit {tag_key} tag"))

        # Method 2: path pattern
        pattern = rules.get("pattern")
        if isinstance(pattern, str) and _match_pattern(pattern, rel_path):
            probes.append((0.85, "path", f"Matches path pattern '{pattern}'"))
        elif isinstance(pattern, list):
            for p in pattern:
                if isinstance(p, str) and _match_pattern(p, rel_path):
                    probes.append((0.85, "path", f"Matches path pattern '{p}'"))
                    break

        # Method 3: content heuristics
        probes_content = 0
        content_rationale = []

        max_lines = rules.get("max_lines")
        if isinstance(max_lines, (int, float)) and max_lines > 0:
            if line_count <= max_lines:
                probes_content += 1
                content_rationale.append(f"{line_count} lines <= {max_lines}")

        min_lines = rules.get("min_lines")
        if isinstance(min_lines, (int, float)) and min_lines > 0:
            if line_count >= min_lines:
                probes_content += 1
                content_rationale.append(f"{line_count} lines >= {min_lines}")

        min_pointer_density = rules.get("min_pointer_density")
        if isinstance(min_pointer_density, (int, float)):
            if p_density >= min_pointer_density:
                probes_content += 1
                content_rationale.append(f"pointer density {p_density:.2f} >= {min_pointer_density}")

        max_heading_depth = rules.get("max_heading_depth")
        if isinstance(max_heading_depth, (int, float)):
            if h_depth <= max_heading_depth:
                probes_content += 1
                content_rationale.append(f"max heading h{h_depth} <= h{int(max_heading_depth)}")

        if probes_content >= 2:
            probes.append((0.70 + probes_content * 0.05, "content", "; ".join(content_rationale)))

        # Method 4: type-based
        rule_type = rules.get("type")
        if rule_type and fm_type:
            if isinstance(rule_type, str) and fm_type == rule_type:
                probes.append((0.75, "type", f"type='{fm_type}' matches '{rule_type}'"))
            elif isinstance(rule_type, list) and fm_type in rule_type:
                probes.append((0.75, "type", f"type='{fm_type}' in {rule_type}"))

        # Best probe for this layer
        for confidence, method, rationale in probes:
            if confidence > best_confidence:
                best_confidence = confidence
                best = {
                    "layer": name,
                    "label": spec.get("label", name),
                    "confidence": confidence,
                    "method": method,
                    "rationale": rationale,
                }

    return best


def classify_vault(
    vault_path: str | Path,
    taxonomy_path: str | Path,
    progress_cb=None,
) -> dict:
    """Classify all files in a vault using a taxonomy file.

    Returns:
        {
            "action": "marginalia-layer-classify",
            "layers": {"L0": {"label": "...", "files": [...], "count": N}, ...},
            "unclassified": [...],
            "stats": {...},
        }
    """
    vault = Path(vault_path)
    taxonomy = load_taxonomy(taxonomy_path)
    md_files = find_md_files(vault)

    classification = {}
    for name, spec in taxonomy.items():
        classification[name] = {
            "label": spec.get("label", name),
            "description": spec.get("description", ""),
            "files": [],
            "count": 0,
        }

    unclassified = []
    by_method = {"tag": 0, "path": 0, "content": 0, "type": 0, "llm": 0}

    total = len(md_files)
    for i, fp in enumerate(md_files):
        if progress_cb:
            progress_cb(i + 1, total)

        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            unclassified.append({"path": str(fp.relative_to(vault)).replace("\\", "/"), "reason": "read_error"})
            continue

        rel_path = str(fp.relative_to(vault)).replace("\\", "/")
        fm = parse_frontmatter(content)
        result = classify_file(rel_path, content, fm, taxonomy)

        if result is None:
            unclassified.append({"path": rel_path, "reason": "no_layer_match"})
            continue

        layer_name = result["layer"]
        by_method[result["method"]] = by_method.get(result["method"], 0) + 1

        entry = {
            "path": rel_path,
            "title": fm.get("title", "") if fm else "",
            "confidence": result["confidence"],
            "method": result["method"],
            "rationale": result["rationale"],
        }
        classification[layer_name]["files"].append(entry)
        classification[layer_name]["count"] = len(classification[layer_name]["files"])

    for name in list(classification.keys()):
        classification[name]["files"].sort(key=lambda f: f["confidence"], reverse=True)

    return {
        "action": "marginalia-layer-classify",
        "version": "1.0.0",
        "vault": str(vault),
        "taxonomy": str(taxonomy_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "unclassified": unclassified,
        "stats": {
            "total_files": total,
            "classified": total - len(unclassified),
            "unclassified": len(unclassified),
            "by_method": by_method,
            "coverage": round((total - len(unclassified)) / total * 100, 1) if total else 0,
        },
    }


def resolve_query(
    query: str,
    vault_path: str | Path,
    taxonomy_path: str | Path,
    top_k: int = 5,
) -> dict:
    """Given a query, resolve which layer(s) and files are most relevant.

    Uses TF-IDF similarity from linker.py to score files, then groups
    by layer for prioritized output. Layer sorting: lower layers first.

    Returns:
        {
            "query": "...",
            "results_by_layer": {"L0": [{path, title, score}...], ...},
            "suggested_order": ["L0", "L1", ...],
        }
    """
    from .linker import _build_corpus as build_linker_index

    vault = Path(vault_path)
    taxonomy = load_taxonomy(taxonomy_path)
    md_files = find_md_files(vault)

    # Build simple content index
    file_contents = {}
    for fp in md_files:
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel_path = str(fp.relative_to(vault)).replace("\\", "/")
        file_contents[rel_path] = content

    # Tokenize query
    qt = _tokenize(query)
    if not qt:
        return {"query": query, "results_by_layer": {}, "suggested_order": []}

    # Score each file with simple TF overlap (fast, no full corpus build)
    scores = {}
    for path, content in file_contents.items():
        tokens = _tokenize(content)
        overlap = len(set(qt) & set(tokens))
        if overlap:
            scores[path] = overlap

    # Classify each file
    by_layer: dict[str, list] = {}
    for path in sorted(scores, key=lambda p: scores[p], reverse=True):
        if len(by_layer.get("_total", [])) >= top_k * 3:
            break
        content = file_contents.get(path, "")
        fm = parse_frontmatter(content)
        result = classify_file(path, content, fm, taxonomy)
        layer = result["layer"] if result else "_unclassified"
        by_layer.setdefault(layer, []).append({
            "path": path,
            "title": fm.get("title", "") if fm else "",
            "score": scores[path],
        })

    # Limit per layer
    for layer in by_layer:
        by_layer[layer] = sorted(by_layer[layer], key=lambda f: f["score"], reverse=True)[:top_k]

    # Suggested order: explicit layers first, then unclassified
    layer_order = [name for name in taxonomy if name in by_layer]
    if "_unclassified" in by_layer:
        layer_order.append("_unclassified")

    return {
        "query": query,
        "results_by_layer": by_layer,
        "suggested_order": layer_order,
    }


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer matching linker.py conventions."""
    # Strip code fences
    clean = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    clean = re.sub(r"`[^`]+`", " ", clean)
    tokens = re.findall(r"[a-zA-Z\u00C0-\u024F]{2,}", clean.lower())
    _STOP = {"the", "and", "for", "that", "this", "with", "from", "have", "are", "was",
             "not", "but", "all", "can", "has", "had", "been", "will", "would", "when",
             "where", "which", "what", "how", "who", "its", "his", "her", "they", "them",
             "their", "our", "your", "into", "over", "after", "before", "between",
             "also", "about", "each", "more", "some", "such", "than", "then", "just",
             "like", "make", "made", "use", "used", "new", "one", "two", "see", "get",
             "set", "may", "now", "way", "say", "said", "come", "came", "take", "took",
             "per", "via", "due", "did", "does", "still", "well", "very", "much", "many"}
    return [t for t in tokens if t not in _STOP and len(t) >= 2]
