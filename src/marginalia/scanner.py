"""Core scanning engine — frontmatter, empty sections, broken links, graph."""

import hashlib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".obsidian", "__pycache__", "dist", "build", ".cache", ".trash"}
SKIP_FILES = {"CHANGELOG.md", "LICENSE.md"}

FM_REQUIRED_DEFAULT = ["title", "tags"]


def _DEFAULT_SCANNER_CONFIG():
    """Return built-in defaults for backward compatibility when no config provided."""
    return {
        "required_tags": ["domain/"],
        "valid_rag_categories": [
            "infra", "git", "governance", "architecture", "security",
            "operations", "history", "agents", "data", "context",
            "mcp", "external", "emergency", "onboarding", "edge_case",
        ],
        "validate_answers": True,
        "valid_statuses": ["active", "draft", "deprecated", "planned", "archived", "superseded"],
    }


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
    """Extract YAML frontmatter fields (no yaml dependency).

    Handles both inline and multi-line YAML formats:
      tags: [a, b]           → inline
      tags:                   → multi-line list
        - a
        - b
    Also handles duplicate frontmatter blocks (keeps the richer one).
    """
    # Find all --- delimited blocks, use the richest one
    matches = list(re.finditer(r"^---\s*\n(.*?)\n---", content, re.DOTALL))
    if not matches:
        return None

    # If multiple frontmatter blocks, pick the one with more fields
    best_fm = None
    best_count = -1
    for m in matches:
        fm_text = m.group(1)
        fields = _parse_fm_block(fm_text)
        if len(fields) > best_count:
            best_fm = fields
            best_count = len(fields)

    return best_fm


def _parse_fm_block(fm_text):
    """Parse a single frontmatter block into a dict."""
    fields = {}
    lines = fm_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match key: value (inline)
        m = re.match(r"^(\w[\w.-]*)\s*:\s*(.+)", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            # Don't overwrite with empty/shorter value (handles duplicate keys)
            if key not in fields or (val and len(val) > len(fields.get(key, ""))):
                fields[key] = val
            i += 1
            continue

        # Match key: (no value) followed by list items (  - value)
        m = re.match(r"^(\w[\w.-]*)\s*:\s*$", line)
        if m:
            key = m.group(1)
            list_items = []
            j = i + 1
            while j < len(lines):
                item_match = re.match(r"^\s+-\s+(.+)", lines[j])
                if item_match:
                    list_items.append(item_match.group(1).strip().strip("'\""))
                    j += 1
                elif re.match(r"^\s+", lines[j]) and not re.match(r"^\w", lines[j]):
                    j += 1  # skip indented non-list lines (e.g. multi-line values)
                else:
                    break
            if list_items:
                fields[key] = "[" + ", ".join(list_items) + "]"
            i = j
            continue

        i += 1
    return fields


def extract_tags(fm_fields):
    """Extract tags list from frontmatter.

    Handles both formats:
      tags: [a, b, c]           → inline array
      tags: [a, b, c]           → reconstructed from YAML list by parse_frontmatter
    """
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


def scan_file(filepath, vault_path, file_index=None, required_fields=None, scanner_config=None):
    """Scan a single .md file for quality issues.

    scanner_config: dict from marginalia.yaml with optional keys:
        required_tags: list[str] — tag prefixes required (e.g. ['domain/'])
        valid_rag_categories: list[str] — allowed rag_categories values
        validate_answers: bool — check answers field format
        valid_statuses: list[str] — allowed status values
    If None, uses built-in defaults (backward compatible).
    """
    issues = []
    required_fields = required_fields or FM_REQUIRED_DEFAULT
    scfg = scanner_config if scanner_config is not None else _DEFAULT_SCANNER_CONFIG()

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [{"file": str(filepath), "type": "read_error", "line": 0,
                 "description": f"Cannot read: {e}", "auto_fixable": False}]

    rel_path = str(filepath.relative_to(vault_path)).replace("\\", "/")
    is_template = "template" in rel_path.lower()
    is_archive = "/archive/" in rel_path or rel_path.startswith("archive/")

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

    # 1b. Frontmatter quality — Giro 7b (S156)
    # Frontmatter exists but content is placeholder/stale → RAG indexes garbage metadata
    if fm is not None:
        # Check 1: summary_todo — summary missing, placeholder, or too short
        _PLACEHOLDER_PATTERNS = {"todo", "tbd", "fixme", "xxx", "...", "placeholder", "da completare", "da fare", ">", "|", ">-", "|-"}
        summary_val = fm.get("summary", "")
        if summary_val:
            summary_clean = summary_val.strip().strip("'\"")
            if summary_clean.lower() in _PLACEHOLDER_PATTERNS or len(summary_clean) < 10:
                issues.append({
                    "file": rel_path, "type": "summary_todo", "line": 1,
                    "description": f"Summary is placeholder or too short (<10 chars): \"{summary_clean}\"",
                    "auto_fixable": False,
                })

        # Check 2: stale_draft — status: draft with updated > 30 days ago
        status_val = fm.get("status", "").strip().strip("'\"").lower()
        updated_val = fm.get("updated", "").strip().strip("'\"")
        if status_val == "draft" and updated_val:
            try:
                updated_date = date.fromisoformat(updated_val)
                days_stale = (date.today() - updated_date).days
                if days_stale > 30:
                    issues.append({
                        "file": rel_path, "type": "stale_draft", "line": 1,
                        "description": f"Draft stale: status=draft, last updated {days_stale} days ago ({updated_val})",
                        "auto_fixable": False,
                    })
            except (ValueError, TypeError):
                pass  # unparseable date — not our problem here

        # Check 3: empty_required_fields — id/title/summary present but empty or whitespace
        for field in ("id", "title", "summary"):
            val = fm.get(field, None)
            if val is not None:
                clean = val.strip().strip("'\"").strip()
                if not clean:
                    issues.append({
                        "file": rel_path, "type": "empty_required_fields", "line": 1,
                        "description": f"Field '{field}' is present but empty",
                        "auto_fixable": False,
                    })

    # 1c. RAG-critical fields — status, rag_categories, answers (S167)
    if fm is not None and not is_template and not is_archive:
        _DEFAULT_STATUSES = {"active", "draft", "deprecated", "planned", "archived", "superseded"}
        valid_statuses = set(scfg.get("valid_statuses", [])) or _DEFAULT_STATUSES
        status_raw = fm.get("status", "").strip().strip("'\"").lower()
        if status_raw and status_raw not in valid_statuses:
            issues.append({
                "file": rel_path, "type": "invalid_status", "line": 1,
                "description": f"status '{status_raw}' not in {sorted(valid_statuses)}",
                "auto_fixable": False,
            })

        # rag_categories validation — config-driven, defaults to legacy set
        valid_rag_cats = set(scfg.get("valid_rag_categories", []))
        if valid_rag_cats:
            rag_cats_raw = fm.get("rag_categories", "")
            if rag_cats_raw:
                match_rc = re.search(r"\[([^\]]*)\]", rag_cats_raw)
                cats = [c.strip().strip("'\"") for c in match_rc.group(1).split(",") if c.strip()] if match_rc else []
                if not cats:
                    issues.append({
                        "file": rel_path, "type": "invalid_rag_categories", "line": 1,
                        "description": "rag_categories is present but empty",
                        "auto_fixable": False,
                    })
                else:
                    bad = [c for c in cats if c not in valid_rag_cats]
                    if bad:
                        issues.append({
                            "file": rel_path, "type": "invalid_rag_categories", "line": 1,
                            "description": f"rag_categories contains unknown values: {bad}. Valid: {sorted(valid_rag_cats)}",
                            "auto_fixable": False,
                        })

        # answers validation — config-driven (default: enabled)
        if scfg.get("validate_answers", True):
            answers_raw = fm.get("answers", "")
            if answers_raw:
                match_ans = re.search(r"\[([^\]]*)\]", answers_raw)
                answers = [a.strip().strip("'\"") for a in match_ans.group(1).split(",") if a.strip()] if match_ans else []
                if not answers:
                    issues.append({
                        "file": rel_path, "type": "malformed_answers", "line": 1,
                        "description": "answers is present but empty — remove or add questions",
                        "auto_fixable": False,
                    })
                else:
                    bad_ans = [a for a in answers if not a.endswith("?")]
                    if bad_ans:
                        issues.append({
                            "file": rel_path, "type": "malformed_answers", "line": 1,
                            "description": f"answers should be questions (end with '?'): {bad_ans[:3]}",
                            "auto_fixable": False,
                        })

    # 1d. Required tag prefix — configurable via required_tags (default: ['domain/'])
    required_tags = scfg.get("required_tags", [])
    if fm is not None and required_tags:
        tags = extract_tags(fm)
        for prefix in required_tags:
            has_prefix = any(t.startswith(prefix) for t in tags)
            if not has_prefix and not is_template and not is_archive:
                # Suggest fix: infer from path
                from .fixer import _PATH_DOMAIN_MAP
                suggested = None
                norm_path = rel_path.replace("\\", "/")
                for pfx in sorted(_PATH_DOMAIN_MAP.keys(), key=len, reverse=True):
                    if norm_path.startswith(pfx + "/") or norm_path.startswith(pfx.lower() + "/"):
                        suggested = _PATH_DOMAIN_MAP[pfx]
                        break
                fix_hint = (f"Run: marginalia fix <vault> --giri 6 --taxonomy <taxonomy.yml> --apply"
                            if suggested else
                            "Run: marginalia tags <vault> --analyze to get LLM suggestions")
                issues.append({
                    "file": rel_path, "type": "missing_required_tag", "line": 1,
                    "description": f"No {prefix} tag — required by scanner config",
                    "fix": f"{prefix}{suggested} (path inference)" if suggested else fix_hint,
                    "auto_fixable": suggested is not None,
                })

    # 2. Empty sections (antifragile — GEDI Case #116, S154)
    # A section is truly empty only if there is no text content AND no sub-headings
    # between this heading and the next heading of equal or higher level (or EOF).
    # Sections that delegate to sub-headings are NOT empty (they have structure).
    # Templates and archive files are excluded — empty sections are their purpose.
    # Lines inside code fences (```) are NOT headings — skip them.
    lines = content.split("\n")
    # Pre-compute which lines are inside code fences
    in_fence = [False] * len(lines)
    fence_open = False
    for idx, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            fence_open = not fence_open
        in_fence[idx] = fence_open
    if not is_template and not is_archive:
        for i, line in enumerate(lines):
            if in_fence[i]:
                continue
            heading_match = re.match(r"^(#{1,6}) ", line)
            if not heading_match:
                continue
            level = len(heading_match.group(1))
            has_text = False
            has_sub_heading = False
            # Look forward until next heading at same/higher level or EOF
            for j in range(i + 1, len(lines)):
                if in_fence[j]:
                    has_text = True  # code block = content
                    break
                fwd = lines[j]
                fwd_heading = re.match(r"^(#{1,6}) ", fwd)
                if fwd_heading:
                    fwd_level = len(fwd_heading.group(1))
                    if fwd_level <= level:
                        break  # same or higher level = section boundary
                    has_sub_heading = True
                elif fwd.strip():
                    has_text = True
                    break  # found text content → not empty
            if not has_text and not has_sub_heading:
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


def check_layer_budget(rel_path, content, scfg):
    """Check file against layer budget rules (Giro 6 — Matrioska).

    Consumes classification from scfg['_layer_map'] (rel_path -> layer_name)
    and budget rules from scfg['layer_budgets'] (layer_name -> {max_lines, min_pointer_density}).

    Returns list of issues.
    """
    layer_map = scfg.get("_layer_map", {})
    budgets = scfg.get("layer_budgets", {})
    if not layer_map or not budgets:
        return []

    layer_name = layer_map.get(rel_path)
    if not layer_name:
        return []

    budget = budgets.get(layer_name, {})
    if not budget:
        return []

    issues = []
    line_count = len(content.split("\n"))

    max_lines = budget.get("max_lines")
    if isinstance(max_lines, (int, float)) and max_lines > 0 and line_count > max_lines:
        issues.append({
            "file": rel_path, "type": "layer_budget_exceeded", "line": 1,
            "description": f"{layer_name}: {line_count} lines exceeds budget of {int(max_lines)}",
            "auto_fixable": False,
        })

    min_pointer_density = budget.get("min_pointer_density")
    if isinstance(min_pointer_density, (int, float)) and min_pointer_density > 0:
        links = sum(1 for ln in content.split("\n") if re.search(r"\[\[.+?\]\]|\[.+?\]\(.+?\)", ln))
        density = links / max(line_count, 1)
        if density < min_pointer_density:
            issues.append({
                "file": rel_path, "type": "layer_budget_exceeded", "line": 1,
                "description": f"{layer_name}: pointer density {density:.2f} below minimum {min_pointer_density}",
                "auto_fixable": False,
            })

    return issues


def check_nonna_standard(content, rel_path):
    """Check a guide against the Nonna Standard (6 elementi strutturali).

    Returns (score 0-6, list of checks {name, passed, detail}).
    """
    checks = []

    # 1. Table "Cosa c'e e dove" — any markdown table with at least 2 columns and 2+ data rows
    table_rows = re.findall(r"^\|.+\|.+\|", content, re.MULTILINE)
    has_table = len(table_rows) >= 3  # header + separator + at least 1 data row
    checks.append({"name": "prereq_table", "label": "Tabella prerequisiti",
                   "passed": has_table,
                   "detail": f"{len(table_rows)} table rows found" if has_table else "No markdown table found"})

    # 2. Method-of-approach — ordered list (1. 2. 3.) with at least 3 items
    ordered_items = re.findall(r"^\s*\d+\.\s+\S", content, re.MULTILINE)
    has_method = len(ordered_items) >= 3
    checks.append({"name": "method_approach", "label": "Metodo di approccio",
                   "passed": has_method,
                   "detail": f"{len(ordered_items)} ordered list items" if has_method else "< 3 ordered list items"})

    # 3. At least 3 copy-paste recipes — code blocks (``` ... ```)
    code_blocks = re.findall(r"```[\s\S]*?```", content)
    has_recipes = len(code_blocks) >= 3
    checks.append({"name": "copy_paste_recipes", "label": "3+ ricette copia-incolla",
                   "passed": has_recipes,
                   "detail": f"{len(code_blocks)} code blocks" if has_recipes else f"Only {len(code_blocks)} code blocks"})

    # 4. Troubleshooting table — heading with "troubleshoot" or "error" + table
    has_trouble_heading = bool(re.search(r"^#{1,4}\s+.*(?:roubleshoot|error|problemi|risoluzione)", content, re.MULTILINE | re.IGNORECASE))
    # Find if any table appears after a troubleshooting heading
    trouble_section = False
    if has_trouble_heading:
        sections = re.split(r"^#{1,4}\s+", content, flags=re.MULTILINE)
        for sec in sections:
            if re.match(r".*(?:roubleshoot|error|problemi|risoluzione)", sec, re.IGNORECASE):
                if len(re.findall(r"^\|.+\|.+\|", sec, re.MULTILINE)) >= 3:
                    trouble_section = True
                    break
    checks.append({"name": "troubleshooting", "label": "Tabella troubleshooting",
                   "passed": trouble_section,
                   "detail": "Heading + table found" if trouble_section else "No troubleshooting section with table"})

    # 5. "Da directory esterne" section — heading referencing external/other directories
    has_external = bool(re.search(r"^#{1,4}\s+.*(?:directory esterne|da qualsiasi|da altre directory|da directory|funziona da)",
                                  content, re.MULTILINE | re.IGNORECASE))
    checks.append({"name": "external_dirs", "label": "Sezione directory esterne",
                   "passed": has_external,
                   "detail": "Section found" if has_external else "No external-directory section"})

    # 6. Links to related guides — "Riferimenti" or "Vedi anche" section with links
    has_refs = False
    ref_sections = re.findall(r"^#{1,4}\s+(?:Riferimenti|Vedi anche|Guide correlate|Related).*?\n(.*?)(?=^#{1,4}\s+|\Z)",
                              content, re.DOTALL | re.MULTILINE | re.IGNORECASE)
    for sec_content in ref_sections:
        if re.search(r"\[.+\]\(.+\)|\[\[.+\]\]", sec_content):
            has_refs = True
            break
    checks.append({"name": "related_links", "label": "Link a guide correlate",
                   "passed": has_refs,
                   "detail": "Reference section with links" if has_refs else "No reference section with links"})

    score = sum(1 for c in checks if c["passed"])
    return score, checks


REVIEW_TAG = "quality/review-needed"


def _inventory_cache_path(vault_path):
    """Return the cache file path for tag inventory."""
    return Path(vault_path) / ".marginalia" / "tag-inventory-cache.json"


def _load_inventory_cache(vault_path):
    """Load cached inventory results. Returns dict: {rel_path: {mtime, entry}}."""
    cache_file = _inventory_cache_path(vault_path)
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_inventory_cache(vault_path, cache):
    """Save inventory cache to disk."""
    cache_file = _inventory_cache_path(vault_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def build_tag_inventory(vault_path, taxonomy_path=None, progress_cb=None, incremental=True):
    """Build L0 Tag Inventory via LLM: for each page, suggest tags with reasoning.

    Returns list of per-file entries:
      [{"file": "rel/path.md", "title": "...", "existing_tags": [...],
        "suggested": [{"tag": "domain/x", "reason": "why"}]}]

    Args:
        vault_path: vault root
        taxonomy_path: optional taxonomy YAML for canonical values hint
        progress_cb: optional callback(current, total, filename) for progress
        incremental: if True, skip files unchanged since last run (S155 GEDI #119)
    """
    from .brain import suggest_tags_explained, is_available
    from .tags import load_taxonomy

    if not is_available():
        return {"error": "No LLM API key configured. Set OPENROUTER_API_KEY or MARGINALIA_API_KEY."}

    base = Path(vault_path)
    md_files = find_md_files(base)

    # Load taxonomy for hints
    taxonomy_values = None
    if taxonomy_path:
        namespaces, merges, _ = load_taxonomy(taxonomy_path)
        taxonomy_values = {ns: sorted(vals) for ns, vals in namespaces.items()}
    taxonomy_hint = list(taxonomy_values.keys()) if taxonomy_values else ["domain", "artifact", "process", "tech", "meta"]

    # Incremental: load cache, skip unchanged files (S155 GEDI Case #119)
    cache = _load_inventory_cache(vault_path) if incremental else {}
    cache_hits = 0
    cache_misses = 0

    inventory = []
    for idx, f in enumerate(md_files):
        rel = str(f.relative_to(base)).replace("\\", "/")

        # Skip archive and templates
        if rel.startswith("archive/") or "/archive/" in rel or "template" in rel.lower():
            continue

        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        fm = parse_frontmatter(content)
        existing = extract_tags(fm) if fm else []
        title = fm.get("title", "").strip('"\'') if fm else f.stem

        # Incremental check: skip if file unchanged since cached analysis
        # Use content hash (SHA256) as primary key; fallback to mtime for legacy cache
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        cached = cache.get(rel)
        cache_match = False
        if incremental and cached:
            if cached.get("content_hash") == content_hash:
                cache_match = True
            elif cached.get("mtime") == f.stat().st_mtime:
                cache_match = True  # legacy cache, mtime unchanged
        if cache_match and cached:
            inventory.append(cached["entry"])
            cache_hits += 1
            if progress_cb:
                progress_cb(idx + 1, len(md_files), f"[cached] {rel}")
            continue

        cache_misses += 1
        if progress_cb:
            progress_cb(idx + 1, len(md_files), rel)

        suggested = suggest_tags_explained(
            f, existing_tags=existing,
            taxonomy_hint=taxonomy_hint,
            taxonomy_values=taxonomy_values
        )

        entry = {
            "file": rel,
            "title": title,
            "existing_tags": existing,
            "suggested": suggested or [],
        }
        inventory.append(entry)

        # Update cache with content hash
        cache[rel] = {"content_hash": content_hash, "mtime": f.stat().st_mtime, "entry": entry}

    # Save cache for next run
    if incremental:
        _save_inventory_cache(vault_path, cache)

    # Report cache stats to stderr
    import sys
    if incremental and (cache_hits or cache_misses):
        total = cache_hits + cache_misses
        print(f"\n  Incremental: {cache_hits}/{total} cached, {cache_misses} analyzed via LLM", file=sys.stderr)

    return inventory


def rationalize_tags(vault_path, taxonomy_path=None):
    """Global LLM rationalization: analyze the full tag landscape and propose taxonomy updates.

    Unlike --analyze (per-page), this sees ALL tags across ALL files and proposes:
    1. Non-canonical domain/ values → merge into canonical domains
    2. Zombie namespaces (1-2 refs) → merge into canonical namespaces
    3. Flat tags → namespace assignment
    4. Updated taxonomy YAML (proposed merges + namespace changes)

    Returns dict with proposed changes + reasoning.
    """
    from .brain import _llm_call, is_available
    from .tags import load_taxonomy

    if not is_available():
        return {"error": "No LLM configured. Set OPENROUTER_API_KEY or configure openrouter.sh connector."}

    # Build full tag landscape
    tag_dict = build_tag_dictionary(vault_path)

    # Load current taxonomy
    current_tax = ""
    if taxonomy_path:
        try:
            current_tax = Path(taxonomy_path).read_text(encoding="utf-8")
        except Exception:
            pass

    # Build compact summary for LLM context
    ns_summary = {}
    flat_summary = []
    for entry in tag_dict["tags"]:
        if entry["namespace"]:
            ns = entry["namespace"]
            ns_summary.setdefault(ns, []).append(f"{entry['tag']} ({entry['count']} files)")
        else:
            flat_summary.append(f"{entry['tag']} ({entry['count']} files)")

    # Compact landscape for LLM context (keep under 3000 chars)
    landscape = f"TAG LANDSCAPE: {tag_dict['total']} tags, {tag_dict['namespaced']} namespaced, {tag_dict['flat']} flat\n\n"

    # Namespaced: show namespace with value count + top 5
    for ns, tags in sorted(ns_summary.items(), key=lambda x: -len(x[1]))[:12]:
        vals = [t.split("(")[0].strip().split("/")[-1] for t in tags[:8]]
        landscape += f"{ns}/ ({len(tags)} vals): {', '.join(vals)}\n"
    landscape += "\n"

    # Flat: top 25 only
    landscape += f"FLAT ({len(flat_summary)}): "
    flat_names = [t.split("(")[0].strip() for t in flat_summary[:25]]
    landscape += ", ".join(flat_names) + "\n"

    taxonomy_section = ""
    if current_tax:
        # Only namespaces + first 30 merges
        tax_lines = []
        in_section = None
        merge_count = 0
        for line in current_tax.split("\n"):
            s = line.strip()
            if s in ("namespaces:", "merges:", "case_fixes:"):
                in_section = s
                tax_lines.append(line)
                merge_count = 0
            elif in_section == "namespaces:":
                tax_lines.append(line)
            elif in_section == "merges:" and merge_count < 30:
                tax_lines.append(line)
                merge_count += 1
            elif in_section == "merges:" and merge_count == 30:
                tax_lines.append("  # ... more merges")
                merge_count += 1
        taxonomy_section = "\n\nCURRENT TAXONOMY:\n" + "\n".join(tax_lines)

    prompt = f"""{landscape}{taxonomy_section}

You are a documentation taxonomy expert. Analyze this tag landscape and propose rationalization.

Tasks:
1. DOMAIN MERGES: Which non-canonical domain/ values should merge into the 18 canonical domains? (e.g. domain/control-plane → domain/agents)
2. ZOMBIE NAMESPACES: Which namespaces with 1-5 refs should merge into canonical namespaces? (e.g. gedi/ → domain/gedi, tool/ → tech/)
3. FLAT TAG ASSIGNMENT: For the top 30 flat tags, which namespace should each belong to?
4. PROPOSED MERGES: Output as YAML merges section (alias: canonical_value)

Return JSON:
{{
  "domain_merges": [{{"from": "domain/X", "to": "domain/Y", "reason": "why"}}],
  "namespace_merges": [{{"from": "ns/val", "to": "ns2/val2", "reason": "why"}}],
  "flat_assignments": [{{"tag": "flat", "to": "ns/val", "reason": "why"}}],
  "proposed_yaml_merges": "alias1: canonical1\\nalias2: canonical2\\n..."
}}"""

    result = _llm_call(
        prompt,
        system_prompt="You are a taxonomy rationalization expert. Return valid JSON only.",
        max_tokens=3000
    )

    if not result or result.startswith("[LLM"):
        return {"error": f"LLM call failed: {result}"}

    # Parse JSON from response
    try:
        match = re.search(r"\{.*\}", result, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            parsed["_raw_response"] = result
            return parsed
    except (json.JSONDecodeError, AttributeError):
        pass

    return {"error": "Could not parse LLM response", "_raw_response": result}


def build_synonym_map_from_inventory(inventory):
    """Read a tag inventory and group tags by meaning using their reasons.

    Returns list of synonym groups:
      [{"canonical": "domain/security", "members": [{"tag": "...", "reason": "...", "files": [...]}]}]
    """
    # Collect all suggested tags with their reasons and files
    tag_reasons = {}  # tag → [{"reason": ..., "file": ...}]
    for entry in inventory:
        for s in entry.get("suggested", []):
            tag = s["tag"]
            tag_reasons.setdefault(tag, []).append({
                "reason": s.get("reason", ""),
                "file": entry["file"],
            })

    # Build entries sorted by frequency
    tag_entries = []
    for tag, usages in sorted(tag_reasons.items(), key=lambda x: -len(x[1])):
        ns, val = (tag.split("/", 1) if "/" in tag else (None, tag))
        tag_entries.append({
            "tag": tag,
            "namespace": ns,
            "value": val,
            "count": len(usages),
            "files": [u["file"] for u in usages],
            "reasons": list({u["reason"] for u in usages if u["reason"]}),
        })

    return tag_entries


def build_tag_dictionary(vault_path, file_index=None):
    """Build L0 Tag Dictionary: every tag with count, files, namespace, and synonym candidates.

    Returns dict with:
      - tags: [{tag, namespace, value, count, files}]
      - flat_tags: tags without namespace (need resolution)
      - namespaced_tags: tags with namespace
      - synonym_groups: auto-detected groups of similar tags
    """
    base = Path(vault_path)
    if file_index is None:
        file_index = build_file_index(base)

    # Collect all tags with file lists
    tag_map = {}  # tag → set of files
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(content)
        if not fm:
            continue
        rel = str(f.relative_to(base)).replace("\\", "/")
        for tag in extract_tags(fm):
            tag_map.setdefault(tag, set()).add(rel)

    # Build dictionary entries
    entries = []
    for tag, files in sorted(tag_map.items()):
        ns, val = (tag.split("/", 1) if "/" in tag else (None, tag))
        entries.append({
            "tag": tag,
            "namespace": ns,
            "value": val,
            "count": len(files),
            "files": sorted(files),
        })

    flat = [e for e in entries if e["namespace"] is None]
    namespaced = [e for e in entries if e["namespace"] is not None]

    # Auto-detect synonym candidates: flat tags that look like existing namespaced values
    # e.g. flat "agenti" ≈ namespaced "domain/agents"
    # Strategy: normalize both sides, require minimum similarity to avoid false positives
    # (e.g. "circle-2" should NOT match "domain/ci" just because "ci" is in "circle")
    ns_values = {}  # value → [full tags]
    for e in namespaced:
        ns_values.setdefault(e["value"], []).append(e["tag"])

    def _normalize(s):
        """Strip plurals, hyphens, common suffixes for comparison."""
        s = s.lower().replace("-", "").replace("_", "")
        for suffix in ("tion", "ment", "ing", "ity"):
            if s.endswith(suffix) and len(s) > len(suffix) + 3:
                break
        # Strip trailing s/i for plural (agenti→agent, docs→doc)
        if s.endswith("i") and len(s) > 3:
            s = s[:-1]
        elif s.endswith("s") and len(s) > 3 and not s.endswith("ss"):
            s = s[:-1]
        return s

    def _is_synonym(flat_norm, ns_norm):
        """Two normalized strings are synonyms if they match closely.
        Require: exact match, or one is a prefix of the other AND the shorter is >= 4 chars.
        This avoids 'ci' matching 'circle' while allowing 'agent' matching 'agents'.
        """
        if flat_norm == ns_norm:
            return True
        shorter, longer = sorted([flat_norm, ns_norm], key=len)
        if len(shorter) < 4:
            return False  # too short for prefix match
        return longer.startswith(shorter)

    synonym_groups = []
    for e in flat:
        val_norm = _normalize(e["value"])
        candidates = []
        for ns_val, ns_tags in ns_values.items():
            ns_norm = _normalize(ns_val)
            if _is_synonym(val_norm, ns_norm):
                candidates.extend(ns_tags)
        if candidates:
            synonym_groups.append({
                "flat_tag": e["tag"],
                "flat_count": e["count"],
                "candidates": candidates,
            })

    # --- Smart pre-filtering (S155 GEDI Case #119) ---
    # Phase 1: Auto-prune candidates (singletons with low semantic value)
    # Phase 2: Auto-resolve (synonym with single unambiguous match)
    # Phase 3: Pattern-merge (repo names, UPPERCASE, plural/singular)
    # These reduce LLM work by 60-80% by resolving trivially classifiable tags.

    prune_candidates = []
    auto_resolved = []
    pattern_merges = []

    # All canonical values for pattern matching
    all_canonical = set()
    for ns_name, ns_vals in ns_values.items():
        for v in ns_vals:
            # ns_vals maps value → [full_tags], extract the namespace
            pass
    # Build ns→values from entries
    ns_canonical = {}
    for e in namespaced:
        ns_canonical.setdefault(e["namespace"], set()).add(e["value"])

    for e in flat:
        tag = e["tag"]
        count = e["count"]

        # --- Pattern-merge: UPPERCASE → case fix ---
        if tag.isupper() and tag.lower() in {v for vals in ns_canonical.values() for v in vals}:
            for ns_name, vals in ns_canonical.items():
                if tag.lower() in vals:
                    pattern_merges.append({
                        "flat_tag": tag,
                        "resolved_to": f"{ns_name}/{tag.lower()}",
                        "rule": "uppercase_case_fix",
                        "confidence": 0.95,
                    })
                    break
            continue

        # --- Pattern-merge: prefix-* tags → namespace/ (configurable) ---
        # Detect tags that look like "prefix-value" and try to map them
        if "-" in tag and "/" not in tag:
            parts = tag.split("-", 1)
            suffix = parts[1] if len(parts) == 2 else None
            if suffix and suffix in {v for vals in ns_canonical.values() for v in vals}:
                for ns_name, vals in ns_canonical.items():
                    if suffix in vals:
                        pattern_merges.append({
                            "flat_tag": tag,
                            "resolved_to": f"{ns_name}/{suffix}",
                            "rule": "repo_name_strip_prefix",
                            "confidence": 0.90,
                        })
                        break
            continue

        # --- Auto-resolve: synonym with exactly 1 candidate ---
        syn = next((s for s in synonym_groups if s["flat_tag"] == tag), None)
        if syn and len(syn["candidates"]) == 1:
            auto_resolved.append({
                "flat_tag": tag,
                "resolved_to": syn["candidates"][0],
                "rule": "single_synonym_match",
                "confidence": 0.85,
            })
            continue

        # --- Auto-prune: singleton generic tags (1 file, short, no semantic match) ---
        if count == 1 and not syn:
            prune_candidates.append({
                "flat_tag": tag,
                "file": e["files"][0] if e["files"] else "?",
                "rule": "singleton_no_match",
                "confidence": 0.80,
            })

    return {
        "total": len(entries),
        "namespaced": len(namespaced),
        "flat": len(flat),
        "tags": sorted(entries, key=lambda e: (-e["count"], e["tag"])),
        "synonym_groups": sorted(synonym_groups, key=lambda g: -g["flat_count"]),
        # S155: smart pre-filtering results
        "auto_resolved": auto_resolved,
        "pattern_merges": pattern_merges,
        "prune_candidates": prune_candidates,
    }


def tag_issues(vault_path, issues, dry_run=True):
    """Add REVIEW_TAG to frontmatter of files with issues. Returns stats dict."""
    base = Path(vault_path)
    files_with_issues = sorted({i["file"] for i in issues})
    tagged, skipped, already = 0, 0, 0

    for rel in files_with_issues:
        fpath = base / rel
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped += 1
            continue

        if REVIEW_TAG in content:
            already += 1
            continue

        fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
        if not fm_match:
            skipped += 1
            continue

        fm_text = fm_match.group(2)
        tags_match = re.search(r"^(tags:\s*)\[([^\]]*)\]", fm_text, re.MULTILINE)
        if not tags_match:
            skipped += 1
            continue

        old_tags = tags_match.group(2)
        new_tags = old_tags.rstrip() + ", " + REVIEW_TAG if old_tags.strip() else REVIEW_TAG
        new_line = tags_match.group(1) + "[" + new_tags + "]"
        new_fm = fm_text[:tags_match.start()] + new_line + fm_text[tags_match.end():]
        new_content = fm_match.group(1) + new_fm + fm_match.group(3) + content[fm_match.end():]

        if not dry_run:
            fpath.write_text(new_content, encoding="utf-8")
        tagged += 1

    return {"tagged": tagged, "skipped": skipped, "already": already,
            "total_files": len(files_with_issues), "tag": REVIEW_TAG}


def untag_issues(vault_path, dry_run=True):
    """Remove REVIEW_TAG from all files in vault. Returns count of files cleaned."""
    base = Path(vault_path)
    cleaned = 0
    for fpath in find_md_files(base):
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if REVIEW_TAG not in content:
            continue
        # Remove ", quality/review-needed" or "quality/review-needed, " or standalone
        new_content = content.replace(", " + REVIEW_TAG, "")
        if new_content == content:
            new_content = content.replace(REVIEW_TAG + ", ", "")
        if new_content == content:
            new_content = content.replace(REVIEW_TAG, "")
        if new_content != content:
            if not dry_run:
                fpath.write_text(new_content, encoding="utf-8")
            cleaned += 1
    return cleaned


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
