"""TF-IDF cosine similarity linker — suggests and applies related links for markdown vaults.

Scoring formula (mirrors wiki-related-links.ps1):
  score = cosine_similarity + (0.08 * tag_overlap) + (0.04 * same_dir) + (0.02 * same_top_dir)
"""

import json
import math
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .scanner import find_md_files, parse_frontmatter, extract_tags

# ---------------------------------------------------------------------------
# Stopwords (IT + EN) — same set as the PowerShell original
# ---------------------------------------------------------------------------
_STOPWORDS = {
    # IT
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "del", "dello", "della", "dei", "degli", "delle",
    "a", "ad", "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "in", "con", "su", "per", "tra", "fra",
    "e", "ed", "o", "od", "ma", "che", "come", "se", "non", "si",
    "piu", "più", "meno", "anche", "solo", "tutto", "tutti",
    "questa", "questo", "questi", "quelle", "quelli",
    "quale", "quali", "quando", "dove", "perche", "perché",
    # EN
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on",
    "for", "with", "by", "is", "are", "be", "as", "at", "from", "it",
    "this", "that", "these", "those", "how", "why", "what", "when", "where",
}

_TOKEN_RE = re.compile(r"[a-z0-9àèéìòù_\-]+")
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def _strip_code(text: str) -> str:
    text = _CODE_BLOCK_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return text


def _tokenize(text: str, min_len: int = 3, max_terms: int = 500) -> dict:
    """Return term-frequency dict, honoring min_len, stopwords, and max_terms cap."""
    text = _strip_code(text).lower()
    tf: dict = {}
    for t in _TOKEN_RE.findall(text):
        if len(t) < min_len or t in _STOPWORDS:
            continue
        tf[t] = tf.get(t, 0) + 1
    if len(tf) > max_terms:
        tf = dict(sorted(tf.items(), key=lambda x: -x[1])[:max_terms])
    return tf


def _escape_link(path: str) -> str:
    return path.replace("(", "%28").replace(")", "%29").replace("+", "%2B").replace(" ", "%20")


def _rel_link(from_rel: str, to_rel: str) -> str:
    """Compute a relative markdown link path between two vault-relative paths."""
    from_parts = from_rel.split("/")[:-1]  # directory of source
    to_parts = to_rel.split("/")

    common = 0
    for a, b in zip(from_parts, to_parts):
        if a == b:
            common += 1
        else:
            break

    up = len(from_parts) - common
    down = to_parts[common:]
    parts = [".."] * up + down
    rel = "/".join(parts) if parts else to_parts[-1]
    if not rel.startswith("."):
        rel = "./" + rel
    return _escape_link(rel)


def _extract_existing_links(content: str) -> set:
    """Return set of link targets (raw + unescaped) already in the file."""
    seen = set()
    text = _strip_code(content)
    for m in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", text):
        raw = m.group(2).strip()
        if not raw or re.match(r"^(https?:|mailto:|data:)", raw):
            continue
        no_anchor = raw.split("#")[0].strip()
        if no_anchor:
            seen.add(no_anchor)
            unescaped = no_anchor.replace("%28", "(").replace("%29", ")").replace("%2B", "+").replace("%20", " ")
            seen.add(unescaped)
    return seen


# ---------------------------------------------------------------------------
# Document building
# ---------------------------------------------------------------------------

def _build_doc_from_rel(
    filepath: Path,
    vault_path: Path,
    rel: str,
    min_len: int = 3,
    max_terms: int = 500,
) -> dict | None:
    """Build a document dict with TF vector, given an already-computed rel path."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    dir_rel = "/".join(rel.split("/")[:-1]) or "."

    fm = parse_frontmatter(content)
    title = None
    summary = None
    tags: list = []
    if fm:
        title = fm.get("title", "").strip().strip('"') or None
        summary = fm.get("summary", "").strip() or None
        tags = extract_tags(fm)
    if not title:
        title = filepath.stem

    body = _strip_code(content)
    if len(body) > 20000:
        body = body[:20000]

    text = "\n".join(filter(None, [title, summary, " ".join(tags), body]))
    tf = _tokenize(text, min_len=min_len, max_terms=max_terms)

    return {
        "path": rel,
        "dir": dir_rel,
        "title": title,
        "tags": tags,
        "vault": str(vault_path),
        "tf": tf,
    }


def _build_doc(filepath: Path, vault_path: Path, min_len: int = 3, max_terms: int = 500) -> dict | None:
    """Build a document dict — single-vault convenience wrapper."""
    rel = str(filepath.relative_to(vault_path)).replace("\\", "/")
    return _build_doc_from_rel(filepath, vault_path, rel, min_len=min_len, max_terms=max_terms)


# ---------------------------------------------------------------------------
# TF-IDF vectorisation
# ---------------------------------------------------------------------------

def _build_tfidf(docs: list) -> list:
    n = len(docs)
    df: dict = {}
    for doc in docs:
        for term in doc["tf"]:
            df[term] = df.get(term, 0) + 1

    for doc in docs:
        vec: dict = {}
        sum_sq = 0.0
        for term, tf_val in doc["tf"].items():
            idf = math.log((n + 1.0) / (df[term] + 1.0)) + 1.0
            w = tf_val * idf
            vec[term] = w
            sum_sq += w * w
        doc["vec"] = vec
        doc["norm"] = math.sqrt(sum_sq)
        del doc["tf"]
    return docs


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _cosine(vec_a: dict, norm_a: float, vec_b: dict, norm_b: float) -> float:
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    small, big = (vec_a, vec_b) if len(vec_a) <= len(vec_b) else (vec_b, vec_a)
    dot = sum(v * big[k] for k, v in small.items() if k in big)
    return dot / (norm_a * norm_b)


def _tag_overlap(tags_a: list, tags_b: list) -> int:
    if not tags_a or not tags_b:
        return 0
    set_a = {t.lower() for t in tags_a}
    return sum(1 for t in tags_b if t.lower() in set_a)


def _score(doc_a: dict, doc_b: dict) -> float:
    cos = _cosine(doc_a["vec"], doc_a["norm"], doc_b["vec"], doc_b["norm"])
    tag_ov = _tag_overlap(doc_a["tags"], doc_b["tags"])
    same_dir = 1 if doc_a["dir"] == doc_b["dir"] else 0
    top_a = doc_a["dir"].split("/")[0]
    top_b = doc_b["dir"].split("/")[0]
    same_top = 1 if (top_a and top_a == top_b) else 0
    return cos + (0.08 * tag_ov) + (0.04 * same_dir) + (0.02 * same_top)


# ---------------------------------------------------------------------------
# Apply helpers
# ---------------------------------------------------------------------------

def _apply_see_also(
    filepath: Path,
    doc_rel: str,
    candidates: list,
    heading: str,
    max_add: int,
    min_score: float,
) -> tuple[bool, str, list]:
    """Insert See Also links into a file. Returns (changed, new_content, added_links)."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    existing = _extract_existing_links(content)

    to_add = []
    for c in candidates:
        if len(to_add) >= max_add:
            break
        if c["score"] < min_score:
            continue
        link = _rel_link(doc_rel, c["path"])
        if link in existing:
            continue
        to_add.append({"title": c["title"], "path": c["path"], "link": link, "score": c["score"]})

    if not to_add:
        return False, content, []

    lines = content.splitlines(keepends=False)
    insert_lines = [f"- [{a['title']}]({a['link']})" for a in to_add]
    heading_stripped = heading.strip()

    # Find existing heading block
    start = next(
        (i for i, line in enumerate(lines) if line.strip().lower() == heading_stripped.lower()),
        -1,
    )

    if start < 0:
        # Append at end
        new_lines = lines + ["", heading, ""] + insert_lines
    else:
        # Find end of block (next H2 or EOF)
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^\s*##\s+", lines[j]):
                end = j
                break
        # Insert before end of block
        new_lines = lines[:end] + insert_lines + lines[end:]

    new_content = "\n".join(new_lines).rstrip() + "\n"
    return True, new_content, to_add


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _resolve_vaults(vault_path) -> list[Path]:
    """Normalise vault_path (single or list) to a list of resolved Paths."""
    if isinstance(vault_path, (str, Path)):
        return [Path(vault_path).resolve()]
    return [Path(p).resolve() for p in vault_path]


def _vault_rel(filepath: Path, vaults: list[Path]) -> tuple[str, Path]:
    """
    Return (vault_relative_path, vault_root) for a file.
    For multi-vault, prefixes the path with the vault directory name to avoid collisions.
    """
    for vault in vaults:
        try:
            rel = filepath.relative_to(vault)
            if len(vaults) > 1:
                # prefix with vault folder name to keep paths unique across vaults
                rel_str = vault.name + "/" + str(rel).replace("\\", "/")
            else:
                rel_str = str(rel).replace("\\", "/")
            return rel_str, vault
        except ValueError:
            continue
    # fallback: use absolute path stem
    return str(filepath).replace("\\", "/"), vaults[0]


def build_suggestions(
    vault_path,
    exclude: list | None = None,
    top_k: int = 7,
    min_len: int = 3,
    max_terms: int = 500,
) -> list:
    """
    Compute TF-IDF similarity suggestions for all docs in vault_path.

    vault_path may be a single path or a list of paths (multi-vault).

    Returns list of dicts:
      {path, title, tags, vault, suggestions: [{path, title, score, tag_overlap, same_dir}]}
    """
    vaults = _resolve_vaults(vault_path)
    exclude = exclude or []

    # Build exclude paths relative to each vault (supports both files and dirs)
    exclude_paths = []
    for vault in vaults:
        for e in exclude:
            full = (vault / e).resolve()
            exclude_paths.append(full)

    def is_excluded(f: Path) -> bool:
        resolved = f.resolve()
        for p in exclude_paths:
            if resolved == p:
                return True
            # Check if f is under an excluded directory
            try:
                resolved.relative_to(p)
                return True
            except ValueError:
                pass
        return False

    md_files = [f for f in find_md_files(vaults) if not is_excluded(f)]

    docs = []
    for f in md_files:
        rel_str, vault_root = _vault_rel(f, vaults)
        doc = _build_doc_from_rel(f, vault_root, rel_str, min_len=min_len, max_terms=max_terms)
        if doc:
            docs.append(doc)

    if len(docs) < 2:
        return []

    docs = _build_tfidf(docs)

    results = []
    for i, a in enumerate(docs):
        cands = []
        for j, b in enumerate(docs):
            if i == j:
                continue
            s = _score(a, b)
            if s <= 0.0:
                continue
            cands.append({
                "path": b["path"],
                "title": b["title"],
                "score": round(s, 4),
                "tag_overlap": _tag_overlap(a["tags"], b["tags"]),
                "same_dir": a["dir"] == b["dir"],
            })
        cands.sort(key=lambda x: -x["score"])
        results.append({
            "path": a["path"],
            "title": a["title"],
            "tags": a["tags"],
            "vault": a.get("vault", ""),
            "suggestions": cands[:top_k],
        })

    return results


def run_link(
    vault_path,
    out_json: str = "out/marginalia-suggestions.json",
    exclude: list | None = None,
    top_k: int = 7,
    min_score: float = 0.35,
    max_links: int = 5,
    scope: str = "all",
    apply: bool = False,
    what_if: bool = True,
    heading: str = "## See also",
    link_graph_json: str | None = None,
    apply_out_dir: str | None = None,
) -> dict:
    """
    Main entry point for the `marginalia link` command.

    vault_path may be a single path or a list of paths (multi-vault).
    what_if=True (default): compute changes but do not write files.
    apply=False (default): skip the apply phase entirely.
    """
    vaults = _resolve_vaults(vault_path)
    # For single-vault compatibility keep 'vault' as primary
    vault = vaults[0]
    suggestions = build_suggestions(vaults, exclude=exclude, top_k=top_k)

    result = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "vaultPaths": [str(v).replace("\\", "/") for v in vaults],
        "excluded": exclude or [],
        "docs": len(suggestions),
        "topK": top_k,
        "method": {
            "primary": "tf-idf cosine similarity",
            "boosts": ["tag overlap (×0.08)", "same directory (×0.04)", "same top-level dir (×0.02)"],
        },
        "results": suggestions,
    }

    out_path = Path(out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not apply:
        return result

    # ------------------------------------------------------------------
    # Apply phase (reversible — backups always created)
    # ------------------------------------------------------------------
    orphan_set: set | None = None
    if scope == "orphans-only":
        if link_graph_json and Path(link_graph_json).exists():
            try:
                graph_data = json.loads(Path(link_graph_json).read_text(encoding="utf-8"))
                orphan_set = set(graph_data.get("orphans", []))
            except Exception:
                orphan_set = None
        if orphan_set is None:
            from .scanner import build_graph
            graph = build_graph(vault)
            orphan_set = set(graph.get("orphans", []))

    targets = [
        r for r in suggestions
        if scope == "all" or r["path"] in (orphan_set or set())
    ]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_root = Path(apply_out_dir) if apply_out_dir else vault / "out" / "marginalia-link-apply"
    backup_dir = backup_root / run_id

    changes = []
    had_errors = False

    for t in targets:
        # Resolve file across all vaults (multi-vault: path may be prefixed with vault name)
        doc_path = None
        for v in vaults:
            # Try with vault prefix stripped (multi-vault paths are prefixed with vault.name/)
            rel_path = t["path"]
            if len(vaults) > 1 and rel_path.startswith(v.name + "/"):
                rel_path = rel_path[len(v.name) + 1:]
            candidate = v / Path(rel_path)
            if candidate.exists():
                doc_path = candidate
                break
        if doc_path is None:
            continue

        cands = [c for c in t["suggestions"] if c["score"] >= min_score][:max_links]
        if not cands:
            continue

        changed, new_content, added = _apply_see_also(
            doc_path, t["path"], cands, heading, max_links, min_score
        )
        if not changed:
            continue

        # Always create backup
        backup_file = backup_dir / Path(t["path"])
        backup_file = backup_file.with_suffix(backup_file.suffix + ".bak")
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(doc_path, backup_file)

        changes.append({
            "path": t["path"],
            "backup": str(backup_file).replace("\\", "/"),
            "added": [{"title": a["title"], "path": a["path"], "score": a["score"]} for a in added],
        })

        if not what_if:
            try:
                doc_path.write_text(new_content, encoding="utf-8")
            except Exception as exc:
                had_errors = True
                import sys
                print(f"WARNING: write failed for {t['path']}: {exc}", file=sys.stderr)

    apply_summary = {
        "ok": not had_errors,
        "runId": run_id,
        "whatIf": what_if,
        "scope": scope,
        "minScore": min_score,
        "maxLinksToAdd": max_links,
        "targets": len(targets),
        "changed": len(changes),
        "backupDir": str(backup_dir).replace("\\", "/"),
        "changes": changes,
        "rollback": {"note": "Copy .bak files from backupDir back to their original paths to rollback."},
    }

    if changes or what_if:
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "apply-summary.json").write_text(
            json.dumps(apply_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    result["apply"] = apply_summary
    return result
