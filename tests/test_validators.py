"""Tests for marginalia.validators — Acceptance Criteria & Evaluator-Optimizer pattern."""

import pytest
from marginalia.validators import (
    validate_closeout,
    validate_scan,
    evaluate_with_retry,
)


class TestValidateCloseout:
    def test_valid_closeout(self):
        data = {
            "session_number": 124,
            "title": "GEDI MCP Tokenizer Fix",
            "date": "2026-03-10",
            "repos_scanned": ["wiki", "agents"],
            "commits_found": 5,
            "template": {
                "platform_memory_entry": "x" * 60,
                "chronicle_content": "x" * 120,
                "session_history_line": "| S124 | 2026-03-10 | fix |",
            },
        }
        report = validate_closeout(data)
        assert report["valid"] is True
        assert report["confidence"] == 1.0
        assert len(report["failed"]) == 0
        assert report["total_checks"] == 8

    def test_missing_session_number(self):
        data = {"title": "test", "date": "2026-03-10", "repos_scanned": ["wiki"],
                "commits_found": 0, "template": {
                    "platform_memory_entry": "x" * 60,
                    "chronicle_content": "x" * 120,
                    "session_history_line": "line",
                }}
        report = validate_closeout(data)
        assert report["valid"] is False
        assert any(f["id"] == "AC-01" for f in report["failed"])

    def test_invalid_date_format(self):
        data = {"session_number": 1, "title": "t", "date": "March 10",
                "repos_scanned": ["wiki"], "commits_found": 0,
                "template": {"platform_memory_entry": "x" * 60,
                             "chronicle_content": "x" * 120,
                             "session_history_line": "line"}}
        report = validate_closeout(data)
        assert any(f["id"] == "AC-03" for f in report["failed"])

    def test_empty_repos(self):
        data = {"session_number": 1, "title": "t", "date": "2026-01-01",
                "repos_scanned": [], "commits_found": 0,
                "template": {"platform_memory_entry": "x" * 60,
                             "chronicle_content": "x" * 120,
                             "session_history_line": "line"}}
        report = validate_closeout(data)
        assert any(f["id"] == "AC-04" for f in report["failed"])

    def test_confidence_partial(self):
        # Missing multiple fields
        data = {"title": "test", "date": "bad"}
        report = validate_closeout(data)
        assert 0 < report["confidence"] < 1.0


class TestValidateScan:
    def test_valid_scan(self):
        data = {
            "action": "marginalia-scan",
            "issues": [{"file": "a.md", "type": "missing_frontmatter", "description": "no fm"}],
            "files_scanned": 42,
        }
        report = validate_scan(data)
        assert report["valid"] is True

    def test_missing_action(self):
        data = {"issues": [], "files_scanned": 1}
        report = validate_scan(data)
        assert any(f["id"] == "AC-01" for f in report["failed"])

    def test_bad_issue_structure(self):
        data = {"action": "scan", "issues": [{"only_file": "a.md"}], "files_scanned": 1}
        report = validate_scan(data)
        assert any(f["id"] == "AC-03" for f in report["failed"])


class TestEvaluateWithRetry:
    def test_passes_first_try(self):
        def producer():
            return {"session_number": 1, "title": "t", "date": "2026-01-01",
                    "repos_scanned": ["wiki"], "commits_found": 3,
                    "template": {"platform_memory_entry": "x" * 60,
                                 "chronicle_content": "x" * 120,
                                 "session_history_line": "line"}}

        result = evaluate_with_retry(producer, validate_closeout, max_iterations=2, threshold=0.80)
        assert result["iterations"] == 1
        assert result["requires_human_review"] is False

    def test_fails_below_threshold(self):
        call_count = 0

        def bad_producer():
            nonlocal call_count
            call_count += 1
            return {"title": "only title"}  # Missing most fields

        result = evaluate_with_retry(bad_producer, validate_closeout, max_iterations=2, threshold=0.80)
        assert call_count == 2
        assert result["iterations"] == 2
        assert result["requires_human_review"] is True

    def test_improves_on_retry(self):
        attempt = [0]

        def improving_producer():
            attempt[0] += 1
            if attempt[0] == 1:
                return {"title": "t"}  # Bad
            return {  # Good on second try
                "session_number": 1, "title": "t", "date": "2026-01-01",
                "repos_scanned": ["wiki"], "commits_found": 0,
                "template": {"platform_memory_entry": "x" * 60,
                             "chronicle_content": "x" * 120,
                             "session_history_line": "line"},
            }

        result = evaluate_with_retry(improving_producer, validate_closeout, max_iterations=3, threshold=0.80)
        assert result["iterations"] == 2
        assert result["requires_human_review"] is False
