"""Tests for marginalia.canonical — entity -> canonical source registry.

Bug #1418: RAG source fabrication. Layer 5 of the wiki graph identifies
which file is the SSoT for a given entity so rerankers can boost the right
chunks.
"""

import tempfile
from pathlib import Path

from marginalia.canonical import (
    build_canonical_sources,
    _file_prefix,
    _score_file,
)


def _mk_vault(files):
    """files: {rel_path: content} -> temp vault dir (caller must clean up)."""
    tmp = tempfile.mkdtemp()
    base = Path(tmp)
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return base


class TestFilePrefix:
    def test_dash_separated_filename(self):
        assert _file_prefix("guides/caronte-bridge.md") == "caronte"

    def test_no_dash_returns_none(self):
        assert _file_prefix("guides/components.md") is None

    def test_subdir_no_dash_returns_none(self):
        assert _file_prefix("guides/valentino/architecture.md") is None

    def test_stopword_prefix_returns_none(self):
        assert _file_prefix("adr-0001-foo.md") is None
        assert _file_prefix("guide-setup.md") is None

    def test_short_prefix_returns_none(self):
        assert _file_prefix("ab-cde.md") is None

    def test_normalizes_to_lowercase(self):
        assert _file_prefix("Caronte-Bridge.md") == "caronte"


class TestScoreFile:
    def test_shorter_filename_scores_higher(self):
        bl = {}
        fm = {
            "caronte-bridge.md": {"status": "active"},
            "caronte-dispatcher-orchestra-brief.md": {"status": "active"},
        }
        s_short = _score_file("caronte-bridge.md", bl, fm)
        s_long = _score_file("caronte-dispatcher-orchestra-brief.md", bl, fm)
        assert s_short > s_long

    def test_more_backlinks_score_higher(self):
        bl = {
            "caronte-bridge.md": ["a.md", "b.md", "c.md"],
            "caronte-other.md": [],
        }
        fm = {
            "caronte-bridge.md": {"status": "active"},
            "caronte-other.md": {"status": "active"},
        }
        s_linked = _score_file("caronte-bridge.md", bl, fm)
        s_orphan = _score_file("caronte-other.md", bl, fm)
        assert s_linked > s_orphan

    def test_active_status_beats_archived(self):
        bl = {}
        fm = {
            "a-x.md": {"status": "active"},
            "a-y.md": {"status": "archived"},
        }
        assert _score_file("a-x.md", bl, fm) > _score_file("a-y.md", bl, fm)


class TestBuildCanonicalSources:
    def test_single_file_cluster_skipped(self):
        vault = _mk_vault({
            "caronte-bridge.md": "---\nstatus: active\n---\n",
        })
        result = build_canonical_sources(vault, backlinks={}, file_frontmatter={
            "caronte-bridge.md": {"status": "active"},
        })
        assert "caronte" not in result

    def test_cluster_picks_primary_by_composite_score(self):
        fm = {
            "caronte-bridge.md": {"status": "active"},
            "caronte-room-pattern.md": {"status": "active"},
            "caronte-dispatcher-orchestra-brief.md": {"status": "active"},
        }
        bl = {
            "caronte-bridge.md": ["a.md", "b.md", "c.md", "d.md"],
            "caronte-room-pattern.md": ["a.md"],
            "caronte-dispatcher-orchestra-brief.md": [],
        }
        vault = _mk_vault({k: "" for k in fm.keys()})
        result = build_canonical_sources(
            vault, backlinks=bl, file_frontmatter=fm,
        )
        assert "caronte" in result
        assert result["caronte"]["primary"] == "caronte-bridge.md"
        assert result["caronte"]["cluster_size"] == 3
        secondary = result["caronte"]["secondary"]
        assert "caronte-room-pattern.md" in secondary
        assert "caronte-dispatcher-orchestra-brief.md" in secondary

    def test_multiple_entities_detected(self):
        fm = {
            "caronte-bridge.md": {"status": "active"},
            "caronte-room-pattern.md": {"status": "active"},
            "alfred-architecture-v1.md": {"status": "active"},
            "alfred-prompts.md": {"status": "active"},
        }
        vault = _mk_vault({k: "" for k in fm.keys()})
        result = build_canonical_sources(
            vault, backlinks={}, file_frontmatter=fm,
        )
        assert "caronte" in result
        assert "alfred" in result
        assert result["caronte"]["primary"].startswith("caronte-")
        assert result["alfred"]["primary"].startswith("alfred-")

    def test_bug_1418_regression(self):
        """Regression: Alfred cited components.md / architecture.md (no 'caronte')
        as sources for a Caronte query. Canonical layer must designate the
        actual caronte-* file as primary so the reranker can boost it."""
        fm = {
            "guides/caronte-bridge.md": {"status": "active"},
            "guides/caronte-room-pattern.md": {"status": "active"},
            "guides/valentino/components.md": {"status": "active"},
            "guides/valentino/architecture.md": {"status": "active"},
        }
        bl = {
            "guides/caronte-bridge.md": ["x.md", "y.md"],
        }
        vault = _mk_vault({k: "" for k in fm.keys()})
        result = build_canonical_sources(
            vault, backlinks=bl, file_frontmatter=fm,
        )
        assert "caronte" in result
        assert result["caronte"]["primary"] == "guides/caronte-bridge.md"
        # valentino/components.md has no dash in filename -> not a false-positive entity
        assert "components" not in result
        assert "architecture" not in result
