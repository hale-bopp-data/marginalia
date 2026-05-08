"""Acceptance Criteria validators — Evaluator-Optimizer pattern from Agent Levi.

Ported from Agent Levi (PowerShell L3) to marginalia (Python).
Provides structured validation for command outputs with configurable predicates.

Pattern:
  1. Command produces output dict
  2. Validator checks predicates → pass/fail + confidence
  3. If confidence < threshold, retry (max N iterations)
  4. Flag requires_human_review if below threshold after retries

Usage:
    from marginalia.validators import validate_closeout, validate_scan, evaluate_with_retry

    result = run_closeout(...)
    report = validate_closeout(result)
    # report = {"valid": True, "confidence": 0.95, "passed": [...], "failed": [...]}

    # With retry loop (Evaluator-Optimizer):
    final = evaluate_with_retry(producer_fn, validator_fn, max_iterations=2, threshold=0.80)
"""

from __future__ import annotations

import re
from typing import Any, Callable


# --- Predicate helpers ---

def _is_positive_int(v: Any) -> bool:
    return isinstance(v, int) and v > 0


def _is_nonempty_str(v: Any, min_len: int = 1) -> bool:
    return isinstance(v, str) and len(v.strip()) >= min_len


def _is_nonempty_list(v: Any, min_items: int = 1) -> bool:
    return isinstance(v, list) and len(v) >= min_items


def _is_float_range(v: Any, lo: float = 0.0, hi: float = 1.0) -> bool:
    return isinstance(v, (int, float)) and lo <= float(v) <= hi


# --- Closeout AC predicates (from Levi evaluator_config) ---

CLOSEOUT_PREDICATES = [
    {
        "id": "AC-01",
        "description": "session_number must be a positive integer",
        "check": lambda d: _is_positive_int(d.get("session_number")),
    },
    {
        "id": "AC-02",
        "description": "title must be a non-empty string",
        "check": lambda d: _is_nonempty_str(d.get("title")),
    },
    {
        "id": "AC-03",
        "description": "date must be a valid ISO date (YYYY-MM-DD)",
        "check": lambda d: bool(re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", ""))),
    },
    {
        "id": "AC-04",
        "description": "repos_scanned must be a non-empty list",
        "check": lambda d: _is_nonempty_list(d.get("repos_scanned")),
    },
    {
        "id": "AC-05",
        "description": "template must contain platform_memory_entry",
        "check": lambda d: _is_nonempty_str(d.get("template", {}).get("platform_memory_entry"), min_len=50),
    },
    {
        "id": "AC-06",
        "description": "template must contain chronicle_content (min 100 chars)",
        "check": lambda d: _is_nonempty_str(d.get("template", {}).get("chronicle_content"), min_len=100),
    },
    {
        "id": "AC-07",
        "description": "template must contain session_history_line",
        "check": lambda d: _is_nonempty_str(d.get("template", {}).get("session_history_line")),
    },
    {
        "id": "AC-08",
        "description": "commits_found must be >= 0",
        "check": lambda d: isinstance(d.get("commits_found"), int) and d["commits_found"] >= 0,
    },
]

# --- Scan/Fix AC predicates ---

SCAN_PREDICATES = [
    {
        "id": "AC-01",
        "description": "action must be 'scan' or 'fix'",
        "check": lambda d: d.get("action") in ("scan", "fix", "marginalia-scan", "marginalia-fix"),
    },
    {
        "id": "AC-02",
        "description": "issues must be a list",
        "check": lambda d: isinstance(d.get("issues"), list),
    },
    {
        "id": "AC-03",
        "description": "each issue must have file, type, description fields",
        "check": lambda d: all(
            isinstance(i, dict) and "file" in i and "type" in i and "description" in i
            for i in d.get("issues", [{}])
        ),
    },
    {
        "id": "AC-04",
        "description": "files_scanned must be a positive integer",
        "check": lambda d: _is_positive_int(d.get("files_scanned", d.get("total_files"))),
    },
]


# --- 5-domande rubric helpers (S492 PBI #1801, founder voice S483) ---
#
# Maps the founder's 5 questions to machine-checkable frontmatter fields:
#   1. A cosa serve?     -> purpose      (non-empty string, >= 10 chars)
#   2. Come usarlo?      -> when_to_use  (non-empty string, >= 10 chars)
#   3. Perche?           -> why          (non-empty string, >= 10 chars, references origin)
#   4. Q&A?              -> qa           (list of {q, a} dicts, >= 1 item; or [] explicit)
#   5. Contesto/legame?  -> related      (list of paths, >= 1 item; or [] explicit)
#
# Heuristic regex zero-deps default. LLM semantic check opt-in via `marginalia ai`.
# Doctrine: easyway/wiki/guides/governance/wiki-frontmatter-schema.md

_PLACEHOLDER_TOKENS = {
    "todo", "tbd", "fixme", "xxx", "...", "placeholder",
    "da completare", "da fare", ">", "|", ">-", "|-",
}

_MIN_LEN_5D = 10


def _strip_yaml_value(v):
    """Strip YAML-ish quotes/whitespace and return clean str (or '' if not str-like)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip().strip("'\"").strip()
    return str(v).strip()


def is_valid_purpose(fm: dict) -> bool:
    """purpose: non-empty string, >= 10 chars, not placeholder."""
    val = _strip_yaml_value(fm.get("purpose", ""))
    if len(val) < _MIN_LEN_5D:
        return False
    return val.lower() not in _PLACEHOLDER_TOKENS


def is_valid_when_to_use(fm: dict) -> bool:
    """when_to_use: non-empty string, >= 10 chars, not placeholder."""
    val = _strip_yaml_value(fm.get("when_to_use", ""))
    if len(val) < _MIN_LEN_5D:
        return False
    return val.lower() not in _PLACEHOLDER_TOKENS


def is_valid_why(fm: dict) -> bool:
    """why: non-empty, >= 10 chars, ideally references origin (Bug/PBI/S<N>/doctrine_principle)."""
    val = _strip_yaml_value(fm.get("why", ""))
    if len(val) < _MIN_LEN_5D:
        return False
    if val.lower() in _PLACEHOLDER_TOKENS:
        return False
    return True


def is_valid_qa(fm: dict) -> bool:
    """qa: presence check — list of {q,a} dicts, explicit [] for None-applicable, or string marker.

    Marginalia's minimal YAML parser collapses nested dict-in-list to string form
    like '[q: "..."]'. We accept any non-trivial marker as evidence of intent.
    Deeper semantic validation is opt-in via `marginalia ai rubric --suggest`.
    """
    raw = fm.get("qa", None)
    if raw is None:
        return False
    if isinstance(raw, list):
        if len(raw) == 0:
            return True  # explicit empty list permitted (warn elsewhere)
        return all(
            isinstance(item, dict) and "q" in item and "a" in item
            and _strip_yaml_value(item.get("q", "")) and _strip_yaml_value(item.get("a", ""))
            for item in raw
        )
    if isinstance(raw, str):
        s = raw.strip()
        if s in ("", "[]", "[ ]"):
            return s in ("[]", "[ ]")  # explicit [] ok, empty string fail
        # Minimal-parser truncation: qa: present with non-empty content marker
        return "q:" in s or s.startswith("-") or s.startswith("[")
    return False


def is_valid_related(fm: dict) -> bool:
    """related: list of paths (>=1 recommended), or explicit [] permitted."""
    raw = fm.get("related", None)
    if raw is None:
        return False
    if isinstance(raw, list):
        if len(raw) == 0:
            return True  # standalone explicit ok
        return all(isinstance(p, str) and _strip_yaml_value(p) for p in raw)
    if isinstance(raw, str):
        s = raw.strip()
        if s in ("[]", "[ ]"):
            return True
        return "[" in s or s.startswith("-") or "/" in s
    return False


RUBRIC_5D_PREDICATES = [
    {
        "id": "5D-01",
        "field": "purpose",
        "description": "purpose: 1 frase operativa (>=10 chars, no placeholder)",
        "check": lambda fm: is_valid_purpose(fm),
    },
    {
        "id": "5D-02",
        "field": "when_to_use",
        "description": "when_to_use: trigger concreto (>=10 chars, no placeholder)",
        "check": lambda fm: is_valid_when_to_use(fm),
    },
    {
        "id": "5D-03",
        "field": "why",
        "description": "why: rationale + origine Bug/PBI/S<N>/doctrine_principle (>=10 chars)",
        "check": lambda fm: is_valid_why(fm),
    },
    {
        "id": "5D-04",
        "field": "qa",
        "description": "qa: lista [{q,a}] o [] esplicito",
        "check": lambda fm: is_valid_qa(fm),
    },
    {
        "id": "5D-05",
        "field": "related",
        "description": "related: lista path o [] esplicito",
        "check": lambda fm: is_valid_related(fm),
    },
]


def validate_5d_rubric(fm: dict) -> dict:
    """Validate frontmatter against 5-domande rubric. Returns report dict.

    Returns:
      {
        "valid": bool,
        "confidence": float (0-1),
        "passed": [{id, field, description}, ...],
        "failed": [{id, field, description}, ...],
        "total_checks": 5,
      }
    """
    return _run_predicates(fm or {}, RUBRIC_5D_PREDICATES)


# --- Validator engine ---

def _run_predicates(data: dict, predicates: list[dict]) -> dict:
    """Run a list of predicates against data. Returns validation report."""
    passed = []
    failed = []

    for pred in predicates:
        try:
            result = pred["check"](data)
        except Exception:
            result = False

        entry = {"id": pred["id"], "description": pred["description"]}
        if "field" in pred:
            entry["field"] = pred["field"]
        if result:
            passed.append(entry)
        else:
            failed.append(entry)

    total = len(predicates)
    confidence = len(passed) / total if total > 0 else 0.0

    return {
        "valid": len(failed) == 0,
        "confidence": round(confidence, 2),
        "passed": passed,
        "failed": failed,
        "total_checks": total,
    }


def validate_closeout(data: dict) -> dict:
    """Validate closeout output against acceptance criteria."""
    return _run_predicates(data, CLOSEOUT_PREDICATES)


def validate_scan(data: dict) -> dict:
    """Validate scan/fix output against acceptance criteria."""
    return _run_predicates(data, SCAN_PREDICATES)


# --- Evaluator-Optimizer loop ---

def evaluate_with_retry(
    producer_fn: Callable[[], dict],
    validator_fn: Callable[[dict], dict],
    max_iterations: int = 2,
    threshold: float = 0.80,
) -> dict:
    """Evaluator-Optimizer pattern: run producer, validate, retry if below threshold.

    Args:
        producer_fn: Function that produces output dict (no args).
        validator_fn: Function that validates output (e.g., validate_closeout).
        max_iterations: Max retry attempts (default: 2, from Levi config).
        threshold: Confidence threshold (default: 0.80, from Levi config).

    Returns:
        Dict with:
            output: the producer's output
            validation: the validation report
            iterations: number of attempts
            requires_human_review: True if confidence stayed below threshold
    """
    best_output = None
    best_validation = None
    best_confidence = -1.0

    for i in range(1, max_iterations + 1):
        output = producer_fn()
        validation = validator_fn(output)
        confidence = validation["confidence"]

        if confidence > best_confidence:
            best_output = output
            best_validation = validation
            best_confidence = confidence

        if confidence >= threshold:
            return {
                "output": output,
                "validation": validation,
                "iterations": i,
                "requires_human_review": False,
            }

    # Below threshold after all iterations
    return {
        "output": best_output,
        "validation": best_validation,
        "iterations": max_iterations,
        "requires_human_review": True,
    }
