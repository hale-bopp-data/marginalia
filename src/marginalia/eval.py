"""marginalia eval — Before/After RAG quality measurement.

Two subcommands:
  snapshot   Build a query-response snapshot from the current vault state.
  compare    Diff two snapshots and report quality delta.

All computation uses the internal TF-IDF engine (zero external dependencies).
Optional Qdrant integration: set QDRANT_URL + QDRANT_API_KEY env vars.

Snapshot format (JSON):
{
  "version": 1,
  "createdAt": "ISO-8601",
  "vaultPaths": [...],
  "docs": 42,
  "topK": 5,
  "queries": [
    {
      "text": "deploy to production",
      "expected": ["guides/deploy.md"],   // optional ground truth
      "results": [
        {"path": "...", "title": "...", "score": 0.712}
      ],
      "metrics": {
        "top1_score": 0.712,
        "mean_score": 0.55,
        "found": true,           // any result above min_score
        "precision_at_k": 1.0,  // only if expected provided
        "recall_at_k": 1.0
      }
    }
  ],
  "summary": {
    "avg_top1_score": 0.65,
    "avg_mean_score": 0.50,
    "coverage": 0.85,            // fraction of queries with >=1 result above min_score
    "precision_at_k": 0.70,     // macro-avg, only queries with expected
    "recall_at_k": 0.80
  }
}
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .linker import _build_tfidf, _cosine, _tag_overlap, _tokenize, _strip_code, _resolve_vaults
from .scanner import find_md_files, parse_frontmatter, extract_tags

# ---------------------------------------------------------------------------
# Queries loader (minimal YAML, same approach as config.py)
# ---------------------------------------------------------------------------

def _load_queries(path: str | Path) -> list[dict]:
    """
    Load queries from a YAML or JSON file.

    YAML format:
      queries:
        - text: "deploy to production"
          expected: ["guides/deploy.md"]
        - text: "secrets management"

    JSON format:
      [{"text": "...", "expected": [...]}, ...]
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Queries file not found: {p}")

    text = p.read_text(encoding="utf-8", errors="replace")

    # Try JSON first
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        raw = json.loads(text)
        if isinstance(raw, list):
            return [_norm_query(q) for q in raw]
        if isinstance(raw, dict) and "queries" in raw:
            return [_norm_query(q) for q in raw["queries"]]
        raise ValueError("JSON must be a list or {queries: [...]}")

    # Minimal YAML parser for our format
    return _parse_queries_yaml(text)


def _norm_query(q: Any) -> dict:
    if isinstance(q, str):
        return {"text": q, "expected": []}
    return {
        "text": str(q.get("text", q.get("query", ""))).strip(),
        "expected": list(q.get("expected", q.get("ground_truth", []))),
    }


def _parse_queries_yaml(text: str) -> list[dict]:
    """Parse the simple queries YAML format."""
    queries: list[dict] = []
    current: dict | None = None
    in_expected = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- text:"):
            if current is not None:
                queries.append(current)
            current = {"text": stripped[len("- text:"):].strip().strip('"').strip("'"), "expected": []}
            in_expected = False

        elif stripped.startswith("text:") and current is None:
            current = {"text": stripped[5:].strip().strip('"').strip("'"), "expected": []}
            in_expected = False

        elif stripped.startswith("expected:") and current is not None:
            rest = stripped[len("expected:"):].strip()
            if rest.startswith("["):
                # Inline: expected: ["a.md", "b.md"]
                inner = rest.strip("[]")
                current["expected"] = [
                    s.strip().strip('"').strip("'")
                    for s in inner.split(",")
                    if s.strip()
                ]
                in_expected = False
            else:
                in_expected = True

        elif in_expected and stripped.startswith("- ") and current is not None:
            current["expected"].append(stripped[2:].strip().strip('"').strip("'"))

        elif re.match(r"^-\s+text:", stripped) or re.match(r"^queries:", stripped):
            in_expected = False

    if current is not None:
        queries.append(current)

    return [q for q in queries if q.get("text")]


# ---------------------------------------------------------------------------
# Query engine — TF-IDF against vault corpus
# ---------------------------------------------------------------------------

def _build_corpus(vault_paths: list[Path]) -> list[dict]:
    """Build TF-IDF corpus from vault(s)."""
    from .linker import _build_doc_from_rel, _vault_rel

    md_files = find_md_files(vault_paths)
    docs = []
    for f in md_files:
        rel, vault_root = _vault_rel(f, vault_paths)
        doc = _build_doc_from_rel(f, vault_root, rel)
        if doc:
            docs.append(doc)

    if len(docs) < 1:
        return []

    return _build_tfidf(docs)


def _query_corpus(query_text: str, corpus: list[dict], top_k: int, min_score: float) -> list[dict]:
    """Run a text query against the TF-IDF corpus."""
    # Build query vector using same tokenizer
    q_tf = _tokenize(query_text)
    if not q_tf:
        return []

    # Compute IDF using corpus (approximate: use existing doc vectors)
    # We treat the query as a pseudo-document — compute its norm against corpus terms
    # Since we don't have raw tf for corpus docs, we use the tfidf vec directly
    # Simple approach: compute cosine between query TF and each doc's TF-IDF vec

    # Build query tfidf (use corpus df for proper IDF)
    n = len(corpus)
    # Collect df from corpus vecs (terms present = df >= 1)
    df: dict[str, int] = {}
    for doc in corpus:
        for term in doc["vec"]:
            df[term] = df.get(term, 0) + 1

    q_vec: dict[str, float] = {}
    q_sum_sq = 0.0
    for term, tf_val in q_tf.items():
        idf = math.log((n + 1.0) / (df.get(term, 0) + 1.0)) + 1.0
        w = tf_val * idf
        q_vec[term] = w
        q_sum_sq += w * w
    q_norm = math.sqrt(q_sum_sq)

    results = []
    for doc in corpus:
        score = _cosine(q_vec, q_norm, doc["vec"], doc["norm"])
        if score > 0.0:
            results.append({
                "path": doc["path"],
                "title": doc["title"],
                "score": round(score, 4),
            })

    results.sort(key=lambda x: -x["score"])
    return [r for r in results[:top_k] if r["score"] >= min_score]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _query_metrics(results: list[dict], expected: list[str], top_k: int, min_score: float) -> dict:
    scores = [r["score"] for r in results]
    found_paths = {r["path"] for r in results}

    metrics: dict = {
        "top1_score": round(scores[0], 4) if scores else 0.0,
        "mean_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "results_count": len(results),
        "found": len(results) > 0,
    }

    if expected:
        hits = sum(1 for e in expected if any(e in p or p.endswith(e) for p in found_paths))
        metrics["precision_at_k"] = round(hits / min(top_k, max(len(results), 1)), 4)
        metrics["recall_at_k"] = round(hits / len(expected), 4)
        metrics["hits"] = hits

    return metrics


def _summary_metrics(query_results: list[dict]) -> dict:
    top1_scores = [q["metrics"]["top1_score"] for q in query_results]
    mean_scores = [q["metrics"]["mean_score"] for q in query_results]
    found_count = sum(1 for q in query_results if q["metrics"]["found"])

    summary: dict = {
        "queries": len(query_results),
        "avg_top1_score": round(sum(top1_scores) / max(len(top1_scores), 1), 4),
        "avg_mean_score": round(sum(mean_scores) / max(len(mean_scores), 1), 4),
        "coverage": round(found_count / max(len(query_results), 1), 4),
        "queries_with_results": found_count,
    }

    # Macro-avg precision/recall for queries that have expected
    p_list = [q["metrics"]["precision_at_k"] for q in query_results if "precision_at_k" in q["metrics"]]
    r_list = [q["metrics"]["recall_at_k"] for q in query_results if "recall_at_k" in q["metrics"]]
    if p_list:
        summary["precision_at_k"] = round(sum(p_list) / len(p_list), 4)
        summary["recall_at_k"] = round(sum(r_list) / len(r_list), 4)
        summary["evaluated_queries"] = len(p_list)

    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_snapshot(
    vault_path,
    queries_file: str | Path,
    out_path: str | Path,
    top_k: int = 5,
    min_score: float = 0.10,
) -> dict:
    """
    Build a RAG quality snapshot for the current vault state.

    vault_path: single path or list of paths
    queries_file: path to queries.yaml or queries.json
    out_path: where to write the snapshot JSON
    top_k: top-K results per query
    min_score: minimum score to include a result
    """
    vaults = _resolve_vaults(vault_path)
    queries = _load_queries(queries_file)

    if not queries:
        raise ValueError(f"No queries found in {queries_file}")

    corpus = _build_corpus(vaults)
    if not corpus:
        raise ValueError("No documents found in vault(s) — cannot build corpus")

    query_results = []
    for q in queries:
        results = _query_corpus(q["text"], corpus, top_k=top_k, min_score=min_score)
        metrics = _query_metrics(results, q.get("expected", []), top_k=top_k, min_score=min_score)
        query_results.append({
            "text": q["text"],
            "expected": q.get("expected", []),
            "results": results,
            "metrics": metrics,
        })

    summary = _summary_metrics(query_results)

    snapshot = {
        "version": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "vaultPaths": [str(v).replace("\\", "/") for v in vaults],
        "docs": len(corpus),
        "topK": top_k,
        "minScore": min_score,
        "queriesFile": str(Path(queries_file).resolve()).replace("\\", "/"),
        "summary": summary,
        "queries": query_results,
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    return snapshot


def run_compare(before_path: str | Path, after_path: str | Path) -> dict:
    """
    Compare two snapshots and return a delta report.

    Metrics reported per query and in aggregate:
    - score_delta: after - before
    - coverage_delta
    - precision/recall delta (if expected present)
    - new results: paths that appear only in after
    - lost results: paths that appear only in before
    """
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))

    b_queries = {q["text"]: q for q in before.get("queries", [])}
    a_queries = {q["text"]: q for q in after.get("queries", [])}

    query_deltas = []
    for text, a_q in a_queries.items():
        b_q = b_queries.get(text)
        if b_q is None:
            # New query in after — no before to compare
            query_deltas.append({
                "text": text,
                "status": "new_query",
                "after_metrics": a_q["metrics"],
            })
            continue

        b_paths = {r["path"] for r in b_q.get("results", [])}
        a_paths = {r["path"] for r in a_q.get("results", [])}

        b_m = b_q["metrics"]
        a_m = a_q["metrics"]

        delta: dict = {
            "text": text,
            "status": "compared",
            "top1_score_delta": round(a_m["top1_score"] - b_m["top1_score"], 4),
            "mean_score_delta": round(a_m["mean_score"] - b_m["mean_score"], 4),
            "results_count_delta": a_m["results_count"] - b_m["results_count"],
            "new_results": sorted(a_paths - b_paths),
            "lost_results": sorted(b_paths - a_paths),
            "before_metrics": b_m,
            "after_metrics": a_m,
        }

        if "precision_at_k" in a_m and "precision_at_k" in b_m:
            delta["precision_delta"] = round(a_m["precision_at_k"] - b_m["precision_at_k"], 4)
            delta["recall_delta"] = round(a_m["recall_at_k"] - b_m["recall_at_k"], 4)

        query_deltas.append(delta)

    # Queries only in before (removed)
    for text in b_queries:
        if text not in a_queries:
            query_deltas.append({
                "text": text,
                "status": "removed_query",
                "before_metrics": b_queries[text]["metrics"],
            })

    # Aggregate delta
    compared = [d for d in query_deltas if d["status"] == "compared"]
    agg: dict = {
        "queries_compared": len(compared),
        "queries_new": sum(1 for d in query_deltas if d["status"] == "new_query"),
        "queries_removed": sum(1 for d in query_deltas if d["status"] == "removed_query"),
    }

    if compared:
        agg["avg_top1_score_delta"] = round(
            sum(d["top1_score_delta"] for d in compared) / len(compared), 4
        )
        agg["avg_mean_score_delta"] = round(
            sum(d["mean_score_delta"] for d in compared) / len(compared), 4
        )
        b_cov = before.get("summary", {}).get("coverage", 0)
        a_cov = after.get("summary", {}).get("coverage", 0)
        agg["coverage_delta"] = round(a_cov - b_cov, 4)

        p_deltas = [d["precision_delta"] for d in compared if "precision_delta" in d]
        if p_deltas:
            agg["avg_precision_delta"] = round(sum(p_deltas) / len(p_deltas), 4)
            r_deltas = [d["recall_delta"] for d in compared if "recall_delta" in d]
            agg["avg_recall_delta"] = round(sum(r_deltas) / len(r_deltas), 4)

        # Verdict
        score_delta = agg["avg_top1_score_delta"]
        if score_delta > 0.05:
            agg["verdict"] = "IMPROVED"
        elif score_delta < -0.05:
            agg["verdict"] = "DEGRADED"
        else:
            agg["verdict"] = "NEUTRAL"

    return {
        "version": 1,
        "comparedAt": datetime.now(timezone.utc).isoformat(),
        "before": {
            "createdAt": before.get("createdAt"),
            "docs": before.get("docs"),
            "summary": before.get("summary"),
        },
        "after": {
            "createdAt": after.get("createdAt"),
            "docs": after.get("docs"),
            "summary": after.get("summary"),
        },
        "aggregate": agg,
        "queries": query_deltas,
    }
