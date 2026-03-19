"""Tests for marginalia.scanner — Giro 7b frontmatter quality checks."""

import pytest
from datetime import date, timedelta
from marginalia.scanner import scan_file, parse_frontmatter
from pathlib import Path
import tempfile
import os


def _scan_content(content, filename="test.md"):
    """Helper: write content to temp file, scan it, return issues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / filename
        filepath.write_text(content, encoding="utf-8")
        return scan_file(filepath, Path(tmpdir))


def _issues_of_type(issues, issue_type):
    """Filter issues by type."""
    return [i for i in issues if i["type"] == issue_type]


# --- summary_todo ---

class TestSummaryTodo:
    def test_summary_todo_literal(self):
        content = "---\ntitle: Test\ntags: []\nsummary: TODO\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1
        assert "placeholder" in issues[0]["description"].lower() or "too short" in issues[0]["description"].lower()

    def test_summary_tbd(self):
        content = "---\ntitle: Test\ntags: []\nsummary: TBD\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1

    def test_summary_fixme(self):
        content = "---\ntitle: Test\ntags: []\nsummary: FIXME\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1

    def test_summary_dots(self):
        content = "---\ntitle: Test\ntags: []\nsummary: '...'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1

    def test_summary_too_short(self):
        content = "---\ntitle: Test\ntags: []\nsummary: abc\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1
        assert "too short" in issues[0]["description"].lower()

    def test_summary_da_completare(self):
        content = "---\ntitle: Test\ntags: []\nsummary: da completare\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 1

    def test_summary_valid_no_issue(self):
        content = "---\ntitle: Test\ntags: []\nsummary: This is a proper summary with enough detail\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 0

    def test_summary_exactly_10_chars(self):
        content = "---\ntitle: Test\ntags: []\nsummary: 0123456789\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 0

    def test_summary_missing_no_issue(self):
        """No summary field at all — not our check (incomplete_frontmatter handles that)."""
        content = "---\ntitle: Test\ntags: []\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "summary_todo")
        assert len(issues) == 0


# --- stale_draft ---

class TestStaleDraft:
    def test_draft_stale_60_days(self):
        old_date = (date.today() - timedelta(days=60)).isoformat()
        content = f"---\ntitle: Test\ntags: []\nstatus: draft\nupdated: '{old_date}'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 1
        assert "60 days" in issues[0]["description"]

    def test_draft_recent_no_issue(self):
        recent_date = (date.today() - timedelta(days=5)).isoformat()
        content = f"---\ntitle: Test\ntags: []\nstatus: draft\nupdated: '{recent_date}'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 0

    def test_draft_exactly_30_days_no_issue(self):
        boundary_date = (date.today() - timedelta(days=30)).isoformat()
        content = f"---\ntitle: Test\ntags: []\nstatus: draft\nupdated: '{boundary_date}'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 0

    def test_draft_31_days_stale(self):
        stale_date = (date.today() - timedelta(days=31)).isoformat()
        content = f"---\ntitle: Test\ntags: []\nstatus: draft\nupdated: '{stale_date}'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 1

    def test_active_status_no_issue(self):
        old_date = (date.today() - timedelta(days=90)).isoformat()
        content = f"---\ntitle: Test\ntags: []\nstatus: active\nupdated: '{old_date}'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 0

    def test_draft_no_updated_no_issue(self):
        """Draft without updated field — can't determine staleness."""
        content = "---\ntitle: Test\ntags: []\nstatus: draft\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 0

    def test_draft_bad_date_no_crash(self):
        """Unparseable date should not crash."""
        content = "---\ntitle: Test\ntags: []\nstatus: draft\nupdated: 'not-a-date'\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "stale_draft")
        assert len(issues) == 0


# --- empty_required_fields ---

class TestEmptyRequiredFields:
    def test_empty_title(self):
        content = "---\ntitle: ''\ntags: []\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 1
        assert "'title'" in issues[0]["description"]

    def test_empty_id(self):
        content = "---\ntitle: Test\ntags: []\nid: ''\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 1
        assert "'id'" in issues[0]["description"]

    def test_empty_summary(self):
        content = "---\ntitle: Test\ntags: []\nsummary: ''\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 1
        assert "'summary'" in issues[0]["description"]

    def test_whitespace_only_title(self):
        content = "---\ntitle: '   '\ntags: []\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 1

    def test_multiple_empty_fields(self):
        content = "---\ntitle: ''\ntags: []\nid: ''\nsummary: ''\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 3

    def test_valid_fields_no_issue(self):
        content = "---\ntitle: A Good Title\ntags: []\nid: my-doc-001\nsummary: This is a proper summary\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 0

    def test_field_not_present_no_issue(self):
        """Fields not in frontmatter at all — not our check."""
        content = "---\ntitle: Test\ntags: []\n---\n\n# Hello\n"
        issues = _issues_of_type(_scan_content(content), "empty_required_fields")
        assert len(issues) == 0


# --- parse_frontmatter edge cases ---

class TestParseFrontmatter:
    def test_basic_parsing(self):
        fm = parse_frontmatter("---\ntitle: Hello\ntags: [a, b]\n---\nBody")
        assert fm is not None
        assert fm["title"] == "Hello"

    def test_no_frontmatter(self):
        fm = parse_frontmatter("# Just a heading\n\nSome text")
        assert fm is None

    def test_quoted_values(self):
        fm = parse_frontmatter("---\ntitle: 'My Title'\nsummary: \"A summary\"\n---\n")
        assert fm is not None
        assert fm["title"] == "'My Title'"

    def test_multiline_tags(self):
        content = "---\ntitle: Test\ntags:\n  - domain/infra\n  - layer/reference\n---\n"
        fm = parse_frontmatter(content)
        assert fm is not None
        assert "domain/infra" in fm["tags"]


# --- integration: no false positives on clean files ---

class TestCleanFileNoIssues:
    def test_clean_file_no_giro7b_issues(self):
        content = (
            "---\n"
            "title: Clean Document\n"
            "tags: [domain/infra, layer/reference]\n"
            "id: clean-001\n"
            "summary: This is a well-written summary with enough detail for RAG indexing\n"
            "status: active\n"
            f"updated: '{date.today().isoformat()}'\n"
            "---\n\n"
            "# Content\n\nSome real content here.\n"
        )
        issues = _scan_content(content)
        giro7b_types = {"summary_todo", "stale_draft", "empty_required_fields"}
        giro7b_issues = [i for i in issues if i["type"] in giro7b_types]
        assert len(giro7b_issues) == 0
