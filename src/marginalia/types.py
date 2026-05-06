"""Document type taxonomy engine — placement enforcement, bottom-up discovery.

Companion to tags.py. Where tags.py classifies tag tokens into namespaces,
types.py classifies markdown files by `type:` frontmatter and verifies they
live in the conventional folder for that type.

Pattern (mirror of tags.py):
- DEFAULT_TYPES: starter taxonomy {type_name: folder_or_canonical_file}
- load_types_taxonomy(yaml_path): YAML override (G16 — YAML wins when present)
- discover_misplaced(vault): bottom-up, emit per-file status
- add_type_to_frontmatter(file): inject inferred type when missing
- fix_placement(vault, src, dst): single-file move via git mv (history preserved)
"""

import re
import shutil
import subprocess
from pathlib import Path

from .scanner import find_md_files, parse_frontmatter

# Default doc-type taxonomy. Each value is the conventional location relative
# to the vault root. Trailing slash = folder; no slash + ".md" = single canonical file.
DEFAULT_TYPES = {
    "runbook":    "Runbooks/",
    "profile":    "profiles/",
    "feedback":   "feedback/",
    "lessons":    "guides/lessons-learned.md",
    "governance": "guides/governance/",
    "vision":     "guides/vision/",
    "guide":      "guides/",
    "chronicle":  "chronicles/",
}

# Heuristics for inferring `type:` from current path when frontmatter is missing.
# Order matters — first match wins. Most-specific first.
PATH_TO_TYPE = [
    (re.compile(r"^Runbooks/"),                    "runbook"),
    (re.compile(r"^profiles/"),                    "profile"),
    (re.compile(r"^feedback/"),                    "feedback"),
    (re.compile(r"^guides/governance/"),           "governance"),
    (re.compile(r"^guides/vision/"),               "vision"),
    (re.compile(r"^guides/lessons-learned\.md$"),  "lessons"),
    (re.compile(r"^chronicles/"),                  "chronicle"),
    (re.compile(r"^guides/"),                      "guide"),
]


def load_types_taxonomy(yaml_path=None):
    """Load doc-type taxonomy from YAML (G16 SSoT when present), else defaults.

    Expected YAML format:
        types:
          runbook: Runbooks/
          profile: profiles/
          custom-kind: misc/custom/
    """
    if yaml_path is None:
        return dict(DEFAULT_TYPES)
    path = Path(yaml_path)
    if not path.exists():
        return dict(DEFAULT_TYPES)

    content = path.read_text(encoding="utf-8")
    types = {}
    in_section = False
    for raw in content.split("\n"):
        s = raw.strip()
        if s.startswith("#") or not s:
            continue
        if s == "types:":
            in_section = True
            continue
        if in_section:
            if not raw.startswith((" ", "\t")):
                in_section = False
                continue
            m = re.match(r"(\w[\w-]*)\s*:\s*(\S+)", s)
            if m:
                types[m.group(1)] = m.group(2).strip().strip("'\"")

    return types or dict(DEFAULT_TYPES)


def _expected_path(type_name, types_map, file_basename):
    """Compute where a file of `type_name` should live, given its basename."""
    target = types_map.get(type_name)
    if target is None:
        return None
    if target.endswith("/"):
        return f"{target}{file_basename}"
    return target  # canonical single-file target (e.g. lessons-learned.md)


def _infer_type_from_path(rel_path):
    """Bottom-up: deduce type from current folder when frontmatter is missing."""
    rp = rel_path.replace("\\", "/")
    for pattern, type_name in PATH_TO_TYPE:
        if pattern.match(rp):
            return type_name
    return None


def _extract_type(fm):
    """Read `type:` from parsed frontmatter (None if absent)."""
    if not fm:
        return None
    raw = fm.get("type", "")
    if not raw:
        return None
    return raw.strip().strip("'\"").strip("[]").strip()


def discover_misplaced(vault_path, types_map=None):
    """Scan vault, return list of {path, declared_type, inferred_type, expected_path, status}.

    status one of:
      - ok:                  declared type matches current placement
      - placement_mismatch:  declared type's expected path differs from current
      - missing_type:        frontmatter present but `type:` absent (inferred from path)
      - no_frontmatter:      no frontmatter block at all
      - unknown:             declared type not in taxonomy
    """
    if types_map is None:
        types_map = dict(DEFAULT_TYPES)

    base = Path(vault_path)
    results = []
    for f in find_md_files(base):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(f.relative_to(base)).replace("\\", "/")
        basename = f.name
        fm = parse_frontmatter(content)
        declared = _extract_type(fm)
        inferred = _infer_type_from_path(rel)

        if fm is None:
            results.append({
                "path": rel,
                "declared_type": None,
                "inferred_type": inferred,
                "expected_path": _expected_path(inferred, types_map, basename) if inferred else None,
                "status": "no_frontmatter",
            })
            continue

        if declared is None:
            results.append({
                "path": rel,
                "declared_type": None,
                "inferred_type": inferred,
                "expected_path": _expected_path(inferred, types_map, basename) if inferred else None,
                "status": "missing_type",
            })
            continue

        if declared not in types_map:
            results.append({
                "path": rel,
                "declared_type": declared,
                "inferred_type": inferred,
                "expected_path": None,
                "status": "unknown",
            })
            continue

        expected = _expected_path(declared, types_map, basename)
        status = "ok" if expected and expected == rel else "placement_mismatch"
        results.append({
            "path": rel,
            "declared_type": declared,
            "inferred_type": inferred,
            "expected_path": expected,
            "status": status,
        })

    return results


def add_type_to_frontmatter(filepath, type_name, dry_run=True):
    """Add `type: <type_name>` to a file's frontmatter. Skip if already present.

    Creates a frontmatter block if missing. Returns True if a change was (or would be) made.
    """
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False

    fm_match = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not fm_match:
        new_content = f"---\ntype: {type_name}\n---\n" + content
    else:
        fm_text = fm_match.group(2)
        if re.search(r"^type\s*:", fm_text, re.MULTILINE):
            return False
        new_fm_text = fm_text.rstrip() + f"\ntype: {type_name}"
        new_content = content.replace(fm_match.group(2), new_fm_text, 1)

    if not dry_run:
        filepath.write_text(new_content, encoding="utf-8")
    return True


def fix_placement(vault_path, src_rel, dst_rel, dry_run=True, use_git=True):
    """Move a file within the vault. Uses `git mv` when use_git=True (history preserved).

    Returns dict {action, src, dst, [reason]}. action one of:
      - would_move:  dry-run, would have moved
      - moved_git:   moved via git mv
      - moved_fs:    moved via shutil (fallback when not a git repo)
      - skip:        not moved (with `reason`)
    """
    base = Path(vault_path)
    src = base / src_rel
    dst = base / dst_rel
    if not src.exists():
        return {"action": "skip", "reason": "source missing", "src": src_rel, "dst": dst_rel}
    if dst.exists():
        return {"action": "skip", "reason": "dest exists", "src": src_rel, "dst": dst_rel}

    if dry_run:
        return {"action": "would_move", "src": src_rel, "dst": dst_rel}

    dst.parent.mkdir(parents=True, exist_ok=True)
    if use_git:
        try:
            subprocess.run(
                ["git", "mv", str(src), str(dst)],
                cwd=str(base), check=True, capture_output=True,
            )
            return {"action": "moved_git", "src": src_rel, "dst": dst_rel}
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    shutil.move(str(src), str(dst))
    return {"action": "moved_fs", "src": src_rel, "dst": dst_rel}


def summarize(misplaced_results):
    """Counts by status — useful for CLI summary output."""
    counts = {}
    for r in misplaced_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts
