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
