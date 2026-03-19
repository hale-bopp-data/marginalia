"""Tests for marginalia.fixer — Giro 7 frontmatter quality fixer."""

import pytest
from datetime import date, timedelta
from pathlib import Path
from marginalia.fixer import (
    giro7_frontmatter_quality,
    giro0_inventory,
    _extract_first_sentence,
    _title_to_summary,
    _set_fm_field,
)
import tempfile
import os


def _make_vault(files):
    """Create a temp vault with given files. Returns (tmpdir, inventory)."""
    tmpdir = tempfile.mkdtemp()
    for name, content in files.items():
        fp = Path(tmpdir) / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    inventory = giro0_inventory(tmpdir)
    return tmpdir, inventory


def _fix_and_read(files, dry_run=False):
    """Run giro7 on a temp vault and return (fixes, file_contents)."""
    tmpdir, inventory = _make_vault(files)
    result = giro7_frontmatter_quality(tmpdir, inventory, dry_run=dry_run)
    contents = {}
    for name in files:
        fp = Path(tmpdir) / name
        if fp.exists():
            contents[name] = fp.read_text(encoding="utf-8")
    return result, contents


# --- _extract_first_sentence ---

class TestExtractFirstSentence:
    def test_normal_body(self):
        content = "---\ntitle: T\n---\n\n# Heading\n\nThis is a proper first sentence with content.\n"
        assert _extract_first_sentence(content) == "This is a proper first sentence with content."

    def test_skips_headings_and_lists(self):
        content = "---\ntitle: T\n---\n\n# H\n\n- item\n- item2\n\nActual content here for extraction.\n"
        assert _extract_first_sentence(content) == "Actual content here for extraction."

    def test_skips_short_lines(self):
        content = "---\ntitle: T\n---\n\nHi\n\nThis is long enough to be a real sentence.\n"
        assert _extract_first_sentence(content) == "This is long enough to be a real sentence."

    def test_truncates_long(self):
        content = "---\ntitle: T\n---\n\n" + "A" * 200 + "\n"
        result = _extract_first_sentence(content)
        assert result.endswith("...")
        assert len(result) <= 120

    def test_empty_body_returns_none(self):
        content = "---\ntitle: T\n---\n\n# Only Heading\n"
        assert _extract_first_sentence(content) is None

    def test_strips_markdown_links(self):
        content = "---\ntitle: T\n---\n\nSee the [documentation guide](./docs.md) for details on setup.\n"
        result = _extract_first_sentence(content)
        assert "documentation guide" in result
        assert "[" not in result


# --- _title_to_summary ---

class TestTitleToSummary:
    def test_good_title(self):
        assert _title_to_summary("Agent Design Standards") == "Agent Design Standards"

    def test_short_title_returns_none(self):
        assert _title_to_summary("FAQ") is None

    def test_strips_quotes(self):
        assert _title_to_summary("'My Good Title Here'") == "My Good Title Here"


# --- _set_fm_field ---

class TestSetFmField:
    def test_replace_existing(self):
        content = "---\ntitle: Old\nstatus: draft\n---\nBody"
        result = _set_fm_field(content, "status", "active")
        assert "status: active" in result
        assert "draft" not in result

    def test_add_new_field(self):
        content = "---\ntitle: T\n---\nBody"
        result = _set_fm_field(content, "status", "active")
        assert "status: active" in result

    def test_no_frontmatter_unchanged(self):
        content = "Just text"
        assert _set_fm_field(content, "status", "active") == content

    def test_block_scalar_gt_replaced(self):
        """YAML block scalar > with continuation lines must be fully replaced."""
        content = (
            "---\n"
            "title: Test\n"
            "summary: >\n"
            "  This is a multi-line\n"
            "  block scalar summary.\n"
            "status: active\n"
            "---\nBody"
        )
        result = _set_fm_field(content, "summary", "'New summary here'")
        assert "summary: 'New summary here'" in result
        assert "block scalar" not in result
        assert "multi-line" not in result
        assert "status: active" in result  # next field preserved

    def test_block_scalar_pipe_replaced(self):
        """YAML block scalar | with continuation lines must be fully replaced."""
        content = (
            "---\n"
            "title: Test\n"
            "summary: |\n"
            "  Line one.\n"
            "  Line two.\n"
            "tags: []\n"
            "---\nBody"
        )
        result = _set_fm_field(content, "summary", "'Generated summary'")
        assert "summary: 'Generated summary'" in result
        assert "Line one" not in result
        assert "tags: []" in result

    def test_block_scalar_chomped(self):
        """YAML block scalar >- (chomped) handled."""
        content = (
            "---\n"
            "title: Test\n"
            "summary: >-\n"
            "  Chomped block.\n"
            "status: draft\n"
            "---\nBody"
        )
        result = _set_fm_field(content, "summary", "'Fixed'")
        assert "summary: 'Fixed'" in result
        assert "Chomped" not in result


# --- Stale draft resolution ---

class TestStaleDraftFix:
    def test_indices_promoted_to_active(self):
        old = (date.today() - timedelta(days=60)).isoformat()
        files = {
            "indices/DOMAIN/API.md": f"---\ntitle: API\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# API\n"
        }
        result, contents = _fix_and_read(files)
        assert len(result["fixes"]) == 1
        assert result["fixes"][0]["new_status"] == "active"
        assert "status: active" in contents["indices/DOMAIN/API.md"]

    def test_legacy_webapp_deprecated(self):
        old = (date.today() - timedelta(days=60)).isoformat()
        files = {
            "easyway-webapp/tables.md": f"---\ntitle: Tables\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# T\n"
        }
        result, contents = _fix_and_read(files)
        assert result["fixes"][0]["new_status"] == "deprecated"
        assert "status: deprecated" in contents["easyway-webapp/tables.md"]

    def test_orchestrations_deprecated(self):
        old = (date.today() - timedelta(days=60)).isoformat()
        files = {
            "orchestrations/old-script.md": f"---\ntitle: Old\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# O\n"
        }
        result, contents = _fix_and_read(files)
        assert result["fixes"][0]["new_status"] == "deprecated"

    def test_fossil_deprecated(self):
        """Files > 365 days old without path rule → deprecated."""
        old = (date.today() - timedelta(days=500)).isoformat()
        files = {
            "misc/ancient.md": f"---\ntitle: Ancient\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# A\n"
        }
        result, contents = _fix_and_read(files)
        assert result["fixes"][0]["new_status"] == "deprecated"
        assert "fossil" in result["fixes"][0]["reason"]

    def test_generic_stale_promoted(self):
        """Files > 30 days old without path rule and < 365 days → active."""
        old = (date.today() - timedelta(days=45)).isoformat()
        files = {
            "guides/some-guide.md": f"---\ntitle: Guide\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# G\n"
        }
        result, contents = _fix_and_read(files)
        assert result["fixes"][0]["new_status"] == "active"

    def test_recent_draft_untouched(self):
        """Drafts < 30 days old should not be touched."""
        recent = (date.today() - timedelta(days=5)).isoformat()
        files = {
            "guides/wip.md": f"---\ntitle: WIP\ntags: []\nstatus: draft\nupdated: '{recent}'\n---\n\n# W\n"
        }
        result, _ = _fix_and_read(files)
        assert len(result["fixes"]) == 0

    def test_active_status_untouched(self):
        old = (date.today() - timedelta(days=90)).isoformat()
        files = {
            "guides/ok.md": f"---\ntitle: OK\ntags: []\nstatus: active\nupdated: '{old}'\n---\n\n# O\n"
        }
        result, _ = _fix_and_read(files)
        stale_fixes = [f for f in result["fixes"] if f["action"] == "stale_draft_resolve"]
        assert len(stale_fixes) == 0

    def test_dry_run_no_write(self):
        old = (date.today() - timedelta(days=60)).isoformat()
        files = {
            "indices/META/index.md": f"---\ntitle: T\ntags: []\nstatus: draft\nupdated: '{old}'\n---\n\n# T\n"
        }
        result, contents = _fix_and_read(files, dry_run=True)
        assert len(result["fixes"]) == 1
        # File should NOT be modified in dry_run
        assert "status: draft" in contents["indices/META/index.md"]


# --- Summary generation ---

class TestSummaryFix:
    def test_placeholder_gt_replaced(self):
        files = {
            "test.md": "---\ntitle: Agent Design Standards\ntags: []\nsummary: '>'\n---\n\n# Overview\n\nComprehensive guide to designing agents within the EasyWay platform.\n"
        }
        result, contents = _fix_and_read(files)
        summary_fixes = [f for f in result["fixes"] if f["action"] == "summary_generate"]
        assert len(summary_fixes) == 1
        assert ">" not in contents["test.md"].split("---")[1]  # frontmatter block

    def test_todo_replaced(self):
        files = {
            "test.md": "---\ntitle: My Guide\ntags: []\nsummary: TODO\n---\n\nThis guide explains how to configure the deployment pipeline correctly.\n"
        }
        result, contents = _fix_and_read(files)
        assert result["stats"]["summary_fixed"] == 1

    def test_fallback_to_title(self):
        """If no body sentence is long enough, use title."""
        files = {
            "test.md": "---\ntitle: 'Agent Design Standards Reference'\ntags: []\nsummary: TODO\n---\n\n# H\n\n- list\n- only\n"
        }
        result, _ = _fix_and_read(files)
        if result["fixes"]:
            assert result["fixes"][0]["new_summary"] == "Agent Design Standards Reference"

    def test_valid_summary_untouched(self):
        files = {
            "test.md": "---\ntitle: T\ntags: []\nsummary: 'A perfectly good summary that needs no fixing'\n---\n\n# H\n"
        }
        result, _ = _fix_and_read(files)
        summary_fixes = [f for f in result["fixes"] if f["action"] == "summary_generate"]
        assert len(summary_fixes) == 0

    def test_block_scalar_summary_replaced_cleanly(self):
        """Real-world case: summary: > with continuation lines."""
        files = {
            "test.md": (
                "---\n"
                "title: Lessons Learned\n"
                "tags: []\n"
                "summary: >\n"
                "  Raccolta strutturata di tutte le lessons learned operative.\n"
                "  Ogni lesson nasce da un errore reale.\n"
                "status: active\n"
                "updated: '2026-03-15'\n"
                "---\n\n"
                "# Lessons\n\n"
                "Real content for extraction that is long enough to be used as summary.\n"
            )
        }
        result, contents = _fix_and_read(files)
        content = contents["test.md"]
        # Block scalar lines should be gone
        assert "Raccolta strutturata" not in content.split("---")[1]
        assert "Ogni lesson" not in content.split("---")[1]
        # Status should be preserved
        assert "status: active" in content


# --- Empty field filling ---

class TestEmptyFieldFix:
    def test_empty_id_filled(self):
        files = {
            "my-guide.md": "---\ntitle: My Guide\ntags: []\nid: ''\n---\n\n# Content\n"
        }
        result, contents = _fix_and_read(files)
        assert any(f["action"] == "fill_empty_id" for f in result["fixes"])
        assert "id: my-guide" in contents["my-guide.md"]

    def test_empty_title_filled(self):
        files = {
            "deployment-runbook.md": "---\ntitle: ''\ntags: []\n---\n\n# Deploy\n"
        }
        result, contents = _fix_and_read(files)
        assert any(f["action"] == "fill_empty_title" for f in result["fixes"])
        assert "Deployment Runbook" in contents["deployment-runbook.md"]


# --- Stats ---

class TestGiro7Stats:
    def test_stats_counted(self):
        old = (date.today() - timedelta(days=60)).isoformat()
        files = {
            "indices/A.md": f"---\ntitle: A\ntags: []\nstatus: draft\nupdated: '{old}'\nsummary: TODO\n---\n\nReal content here for summary extraction.\n",
            "ok.md": "---\ntitle: OK\ntags: []\nstatus: active\nsummary: 'Good summary here'\n---\n\n# H\n",
        }
        result, _ = _fix_and_read(files)
        assert result["stats"]["stale_draft_fixed"] == 1
        assert result["stats"]["summary_fixed"] == 1
