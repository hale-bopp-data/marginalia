"""Tests for marginalia.closeout — session closeout data collection and file writing."""

import json
import pytest
from pathlib import Path

from marginalia.closeout import (
    collect_session_data,
    generate_closeout_template,
    write_closeout_files,
    run_closeout,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_base(tmp_path):
    """Base dir with no git repos — simulates missing repos gracefully."""
    return tmp_path


@pytest.fixture()
def fake_base(tmp_path):
    """Base dir with fake wiki structure (no real git, but target files exist)."""
    # Platform operational memory
    pom_dir = tmp_path / "wiki" / "agents"
    pom_dir.mkdir(parents=True)
    (pom_dir / "platform-operational-memory.md").write_text(
        "# Platform Operational Memory\n\nExisting content.\n",
        encoding="utf-8",
    )

    # Chronicles dir + index
    chron_dir = tmp_path / "wiki" / "chronicles"
    chron_dir.mkdir(parents=True)
    (chron_dir / "_index.md").write_text(
        "# Chronicles Index\n\n| Date | Title | Session |\n|------|-------|---------|\n",
        encoding="utf-8",
    )

    # Sessions history (external file simulated inside tmp)
    (tmp_path / "sessions-history.md").write_text(
        "# Sessions History\n\n| Session | Date | Title | PRs |\n|---------|------|-------|-----|\n",
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# collect_session_data
# ---------------------------------------------------------------------------

class TestCollectSessionData:
    def test_no_repos_no_crash(self, empty_base):
        """Base dir with no repos returns empty but valid structure."""
        data = collect_session_data(str(empty_base), 99)
        assert data["session_number"] == 99
        assert data["repos"] == {}
        assert data["recent_commits"] == []
        assert data["pr_numbers"] == []
        assert isinstance(data["date"], str)

    def test_session_title_default(self, empty_base):
        """Without title, defaults to 'Session N'."""
        data = collect_session_data(str(empty_base), 42)
        assert data["session_title"] == "Session 42"

    def test_session_title_custom(self, empty_base):
        """Custom title is preserved."""
        data = collect_session_data(str(empty_base), 42, session_title="My Title")
        assert data["session_title"] == "My Title"


# ---------------------------------------------------------------------------
# generate_closeout_template
# ---------------------------------------------------------------------------

class TestGenerateCloseoutTemplate:
    def test_template_structure(self):
        """Template has all required keys."""
        data = {
            "session_number": 100,
            "session_title": "Test Session",
            "date": "2026-03-08",
            "repos": {},
            "recent_commits": [],
            "commit_summaries": [],
            "pr_numbers": [123, 456],
            "wi_numbers": [],
        }
        tmpl = generate_closeout_template(data)

        assert tmpl["session_number"] == 100
        assert tmpl["date"] == "2026-03-08"
        assert tmpl["title"] == "Test Session"
        assert "platform_memory_entry" in tmpl
        assert "chronicle_content" in tmpl
        assert "chronicle_filename" in tmpl
        assert "chronicles_index_entry" in tmpl
        assert "session_history_line" in tmpl
        assert tmpl["pr_numbers"] == [123, 456]

    def test_chronicle_filename_slug(self):
        """Chronicle filename is date + slugified title."""
        data = {
            "session_number": 50,
            "session_title": "Big Feature & Stuff!",
            "date": "2026-01-15",
            "commit_summaries": [],
            "pr_numbers": [],
        }
        tmpl = generate_closeout_template(data)
        assert tmpl["chronicle_filename"].startswith("2026-01-15-")
        assert "big-feature" in tmpl["chronicle_filename"]
        assert tmpl["chronicle_filename"].endswith(".md")

    def test_template_contains_session_number(self):
        """POM entry and chronicle reference the session number."""
        data = {
            "session_number": 77,
            "session_title": "Session 77",
            "date": "2026-02-20",
            "commit_summaries": ["[wiki] fix something"],
            "pr_numbers": [999],
        }
        tmpl = generate_closeout_template(data)
        assert "Session 77" in tmpl["platform_memory_entry"]
        assert "S77" in tmpl["chronicle_content"]
        assert "#999" in tmpl["platform_memory_entry"]


# ---------------------------------------------------------------------------
# write_closeout_files
# ---------------------------------------------------------------------------

class TestWriteCloseoutFiles:
    def test_writes_all_files(self, fake_base):
        """With valid base, writes POM, chronicle, index, and sessions-history."""
        tmpl = {
            "session_number": 50,
            "date": "2026-03-08",
            "title": "Test Write",
            "platform_memory_entry": "## Session 50 — Test Write\nContent here.\n",
            "chronicle_filename": "2026-03-08-test-write.md",
            "chronicle_content": "---\ntitle: S50\n---\nNarrative.\n",
            "chronicles_index_entry": "| 2026-03-08 | S50 | S50 |",
            "session_history_line": "| S50 | 2026-03-08 | Test | PRs: #1 |",
        }
        sessions_history = str(fake_base / "sessions-history.md")
        files = write_closeout_files(str(fake_base), tmpl, sessions_history)

        assert len(files) == 4

        # POM appended
        pom = (fake_base / "wiki" / "agents" / "platform-operational-memory.md").read_text(encoding="utf-8")
        assert "Session 50" in pom
        assert "Existing content" in pom  # original preserved

        # Chronicle created
        chron = (fake_base / "wiki" / "chronicles" / "2026-03-08-test-write.md").read_text(encoding="utf-8")
        assert "Narrative" in chron

        # Index appended
        idx = (fake_base / "wiki" / "chronicles" / "_index.md").read_text(encoding="utf-8")
        assert "S50" in idx

        # Sessions history appended
        sh = (fake_base / "sessions-history.md").read_text(encoding="utf-8")
        assert "S50" in sh

    def test_no_write_if_missing_targets(self, empty_base):
        """If target files don't exist, no files are written and no crash."""
        tmpl = {
            "platform_memory_entry": "test",
            "chronicle_filename": "test.md",
            "chronicle_content": "test",
            "chronicles_index_entry": "test",
            "session_history_line": "test",
        }
        files = write_closeout_files(str(empty_base), tmpl)
        assert files == []


# ---------------------------------------------------------------------------
# run_closeout (integration)
# ---------------------------------------------------------------------------

class TestRunCloseout:
    def test_dry_run_no_files_written(self, empty_base):
        """Dry run returns valid result but writes nothing."""
        result = run_closeout(str(empty_base), 99, write=False)

        assert result["action"] == "marginalia-closeout"
        assert result["session_number"] == 99
        assert result["mode"] == "DRY RUN"
        assert result["files_written"] == []
        assert "template" in result

    def test_write_mode(self, fake_base):
        """Write mode creates files and reports them."""
        result = run_closeout(
            str(fake_base), 50,
            session_title="Integration Test",
            write=True,
            sessions_history_path=str(fake_base / "sessions-history.md"),
        )

        assert result["mode"] == "WRITE"
        assert len(result["files_written"]) == 4
        assert result["title"] == "Integration Test"
