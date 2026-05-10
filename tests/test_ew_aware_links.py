"""Tests for marginalia EW-aware link extraction (PBI #1966).

Covers:
  - Backtick code path links: `path/to/file.md`
  - Frontmatter YAML link keys: related, superseded_by, see_also, parent, children, documents
  - --external-linkers: files outside vault that link into it
  - --vault-root-prefix: normalize absolute workspace paths to vault-relative
  - --ew-aware opt-in batch
  - --no-ew-aware override (default behaviour)
"""

import json
import tempfile
from pathlib import Path

import pytest

from marginalia.scanner import (
    _extract_backtick_codepath_links,
    _extract_backtick_compound_word_refs,
    _extract_frontmatter_links,
    build_file_index,
    build_graph,
    parse_frontmatter,
)


# --- Compound-word backtick (no .md suffix) ---

class TestBacktickCompoundWord:
    def test_underscore_compound(self):
        content = "Ref `feedback_acknowledge_before_workaround` here."
        assert _extract_backtick_compound_word_refs(content) == ["feedback_acknowledge_before_workaround"]

    def test_hyphen_compound(self):
        content = "Ref `agent-cassio` and `lessons-mcp`."
        out = _extract_backtick_compound_word_refs(content)
        assert "agent-cassio" in out and "lessons-mcp" in out

    def test_no_separator_word_skipped(self):
        # Plain word without _ or - must NOT be captured (avoids `marginalia`, `config`)
        content = "Use `marginalia` and `config` and `build`."
        assert _extract_backtick_compound_word_refs(content) == []

    def test_compound_resolves_to_basename_in_vault(self, tmp_path):
        """Vault file `agent-cassio.md` is referenced via `agent-cassio` backtick."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "guides").mkdir()
        (vault / "guides" / "agent-cassio.md").write_text(
            "---\ntitle: Cassio\ntags: []\n---\n", encoding="utf-8")
        (vault / "linker.md").write_text(
            "---\ntitle: Linker\ntags: []\n---\n\nSee `agent-cassio` for context.\n",
            encoding="utf-8")
        graph = build_graph(vault, ew_aware=True)
        assert "guides/agent-cassio.md" not in set(graph["orphans"])

    def test_compound_unresolved_does_not_create_false_link(self, tmp_path):
        """Compound backtick referring non-existent file is silently dropped."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "real.md").write_text("---\ntitle: R\ntags: []\n---\n", encoding="utf-8")
        (vault / "linker.md").write_text(
            "---\ntitle: L\ntags: []\n---\n\nRef `not-a-real-file` here.\n",
            encoding="utf-8")
        graph = build_graph(vault, ew_aware=True)
        # real.md still orphan (unrelated), no exception, no false positive
        assert "real.md" in set(graph["orphans"])


# --- Backtick code path extraction (AC1) ---

class TestBacktickCodepath:
    def test_simple_backtick_codepath(self):
        content = "See `easyway/wiki/guides/x.md` for details."
        links = _extract_backtick_codepath_links(content)
        assert links == ["easyway/wiki/guides/x.md"]

    def test_multiple_backticks_same_line(self):
        content = "Refs: `a/b.md` and `c/d.md` here."
        links = _extract_backtick_codepath_links(content)
        assert links == ["a/b.md", "c/d.md"]

    def test_ignores_non_md_extension(self):
        content = "Run `script.sh` or `tool.py`."
        assert _extract_backtick_codepath_links(content) == []

    def test_ignores_word_without_path_or_md(self):
        # plain word without slash or .md suffix should not match
        content = "Use `marginalia` to do stuff."
        assert _extract_backtick_codepath_links(content) == []

    def test_ignores_code_fence_triple_backticks(self):
        # Triple backticks (code fence) must not match
        content = "```\nfoo bar.md\n```"
        # Body inside fence does not have `...md` single-backtick form
        assert _extract_backtick_codepath_links(content) == []

    def test_path_with_dot_only(self):
        # path with dot but no slash, e.g. "./README.md" — should match (path-like via dot)
        content = "Read `./README.md` first."
        assert _extract_backtick_codepath_links(content) == ["./README.md"]

    def test_plain_readme_md_single_word_does_match(self):
        # Edge case: plain `README.md` — has dot, qualifies as path-like.
        # Risk: false-positive in tutorials. Mitigation: scope of EW-aware mode.
        content = "Edit `README.md`."
        assert _extract_backtick_codepath_links(content) == ["README.md"]


# --- Frontmatter link extraction (AC2) ---

class TestFrontmatterLinks:
    def test_related_inline_list(self):
        fm = {"related": "[a.md, b.md]"}
        assert _extract_frontmatter_links(fm) == ["a.md", "b.md"]

    def test_superseded_by_single_value(self):
        fm = {"superseded_by": "newer.md"}
        assert _extract_frontmatter_links(fm) == ["newer.md"]

    def test_multiple_keys(self):
        fm = {
            "related": "[x.md, y.md]",
            "see_also": "[z.md]",
            "parent": "p.md",
        }
        out = _extract_frontmatter_links(fm)
        assert "x.md" in out and "y.md" in out and "z.md" in out and "p.md" in out

    def test_empty_or_missing_keys(self):
        assert _extract_frontmatter_links({}) == []
        assert _extract_frontmatter_links({"related": ""}) == []
        assert _extract_frontmatter_links(None) == []

    def test_quoted_values_stripped(self):
        fm = {"related": "['guide-a.md', \"guide-b.md\"]"}
        out = _extract_frontmatter_links(fm)
        assert out == ["guide-a.md", "guide-b.md"]

    def test_unrelated_keys_ignored(self):
        fm = {"title": "X", "tags": "[domain/foo]", "summary": "..."}
        assert _extract_frontmatter_links(fm) == []


# --- build_graph EW-aware integration ---

@pytest.fixture
def ew_vault(tmp_path):
    """Build a small EW-style vault with backtick + frontmatter + plain links."""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "guides").mkdir()
    (vault / "guides" / "agent-cassio.md").write_text(
        "---\ntitle: Cassio\ntags: [domain/agents]\n---\n\n# Cassio\n",
        encoding="utf-8",
    )
    (vault / "guides" / "doctrine-versioning.md").write_text(
        "---\ntitle: Doctrine Versioning\ntags: [domain/governance]\n---\n\n# Doctrine\n",
        encoding="utf-8",
    )
    (vault / "guides" / "auditor-overview.md").write_text(
        "---\ntitle: Auditor\ntags: [domain/agents]\n"
        "related: [guides/doctrine-versioning.md]\n---\n\n"
        "Refs `guides/agent-cassio.md` for details.\n",
        encoding="utf-8",
    )
    return vault


def test_default_mode_marks_orphans(ew_vault):
    """Without --ew-aware: backtick + frontmatter NOT seen, files appear orphan."""
    graph = build_graph(ew_vault)
    orphans = set(graph["orphans"])
    # auditor-overview.md is itself orphan (no inbound) — but cassio + doctrine
    # are also orphan because their citations are via backtick + frontmatter.
    assert "guides/agent-cassio.md" in orphans
    assert "guides/doctrine-versioning.md" in orphans


def test_ew_aware_mode_resolves_backtick_and_frontmatter(ew_vault):
    """With --ew-aware: backtick + frontmatter links are resolved."""
    graph = build_graph(ew_vault, ew_aware=True)
    orphans = set(graph["orphans"])
    # cassio is now linked via backtick code path in auditor-overview.md
    assert "guides/agent-cassio.md" not in orphans
    # doctrine-versioning is linked via frontmatter "related"
    assert "guides/doctrine-versioning.md" not in orphans


def test_no_ew_aware_explicit_disables(ew_vault):
    """ew_aware=False explicit (override) keeps default behaviour."""
    graph = build_graph(ew_vault, ew_aware=False)
    orphans = set(graph["orphans"])
    assert "guides/agent-cassio.md" in orphans


# --- External linkers (AC3) ---

def test_external_linkers_remove_orphan(tmp_path):
    """File outside vault links into vault → vault file no longer orphan."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "x.md").write_text("---\ntitle: X\ntags: []\n---\n\nbody\n", encoding="utf-8")

    external = tmp_path / "CLAUDE.md"
    external.write_text(
        "---\ntitle: External\ntags: []\n---\n\nSee `x.md` here.\n",
        encoding="utf-8",
    )

    graph = build_graph(vault, ew_aware=True, external_linkers=[external])
    assert "x.md" not in set(graph["orphans"])


def test_external_linkers_dir_walk_skips_blacklist(tmp_path):
    """External linker directory walk skips .git/ .obsidian/ node_modules/."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "y.md").write_text("---\ntitle: Y\ntags: []\n---\n", encoding="utf-8")

    external = tmp_path / "ext"
    external.mkdir()
    (external / "linker.md").write_text("Links: `y.md`\n", encoding="utf-8")
    skip_dir = external / ".git"
    skip_dir.mkdir()
    (skip_dir / "should-not-be-read.md").write_text("nope\n", encoding="utf-8")

    graph = build_graph(vault, ew_aware=True, external_linkers=[external])
    assert "y.md" not in set(graph["orphans"])


# --- Vault root prefix (AC4) ---

def test_vault_root_prefix_normalizes_absolute_paths(tmp_path):
    """Absolute workspace paths (easyway/wiki/x.md) resolve when prefix stripped."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "target.md").write_text("---\ntitle: T\ntags: []\n---\n", encoding="utf-8")

    external = tmp_path / "instructions.md"
    external.write_text(
        "---\ntitle: Inst\ntags: []\n---\n\nSee `easyway/wiki/target.md`.\n",
        encoding="utf-8",
    )

    graph = build_graph(
        vault,
        ew_aware=True,
        external_linkers=[external],
        vault_root_prefix="easyway/wiki/",
    )
    assert "target.md" not in set(graph["orphans"])


# --- AC8: --no-ew-aware override leaves vault behaviour unchanged ---

def test_obsidian_generic_vault_no_regression(tmp_path):
    """Without ew_aware (default), generic Obsidian vault behaves as before.

    Generic vault uses only [text](path.md) and [[wikilinks]]. Backtick and
    frontmatter EW-keys must NOT be parsed → no false positives reverse.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text(
        "---\ntitle: A\ntags: []\nrelated: [b.md]\n---\n\n"
        "# A\n\nIn code: `something/b.md` (just a snippet)\n",
        encoding="utf-8",
    )
    (vault / "b.md").write_text("---\ntitle: B\ntags: []\n---\n\n# B\n", encoding="utf-8")

    graph_default = build_graph(vault)  # ew_aware default False
    # b.md must remain orphan in non-EW mode (backtick + frontmatter not parsed)
    assert "b.md" in set(graph_default["orphans"])

    graph_aware = build_graph(vault, ew_aware=True)
    # When ew_aware enabled, b.md is linked via frontmatter related: + backtick
    assert "b.md" not in set(graph_aware["orphans"])
