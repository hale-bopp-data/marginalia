"""Tests for marginalia.linker — TF-IDF cosine similarity engine."""

import math
import tempfile
from pathlib import Path

import pytest

from marginalia.linker import (
    _cosine,
    _tag_overlap,
    _score,
    _tokenize,
    _rel_link,
    build_suggestions,
)
from marginalia.config import load_config, _parse_yaml


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

def test_tokenize_basic():
    tf = _tokenize("hello world hello")
    assert tf["hello"] == 2
    assert tf["world"] == 1


def test_tokenize_stopwords_removed():
    tf = _tokenize("the quick brown fox")
    assert "the" not in tf
    assert "quick" in tf


def test_tokenize_min_len():
    tf = _tokenize("a bb ccc dddd", min_len=3)
    assert "a" not in tf
    assert "bb" not in tf
    assert "ccc" in tf


def test_tokenize_max_terms():
    # 200 unique words, max_terms=10 → only top 10 kept
    words = [f"word{i}" * (200 - i) for i in range(200)]
    text = " ".join(words)
    tf = _tokenize(text, min_len=1, max_terms=10)
    assert len(tf) <= 10


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

def test_cosine_identical():
    vec = {"a": 1.0, "b": 2.0}
    norm = math.sqrt(1 + 4)
    assert abs(_cosine(vec, norm, vec, norm) - 1.0) < 1e-9


def test_cosine_orthogonal():
    va, vb = {"a": 1.0}, {"b": 1.0}
    assert _cosine(va, 1.0, vb, 1.0) == 0.0


def test_cosine_zero_norm():
    assert _cosine({}, 0.0, {"a": 1.0}, 1.0) == 0.0


# ---------------------------------------------------------------------------
# _tag_overlap
# ---------------------------------------------------------------------------

def test_tag_overlap_none():
    assert _tag_overlap([], ["a", "b"]) == 0


def test_tag_overlap_full():
    assert _tag_overlap(["x", "y"], ["X", "Y"]) == 2  # case-insensitive


def test_tag_overlap_partial():
    assert _tag_overlap(["a", "b", "c"], ["b", "c", "d"]) == 2


# ---------------------------------------------------------------------------
# _rel_link
# ---------------------------------------------------------------------------

def test_rel_link_same_dir():
    link = _rel_link("docs/a.md", "docs/b.md")
    assert link == "./b.md"


def test_rel_link_parent():
    link = _rel_link("docs/sub/a.md", "docs/b.md")
    assert link == "../b.md"


def test_rel_link_root_to_subdir():
    link = _rel_link("a.md", "docs/b.md")
    assert link == "./docs/b.md"


def test_rel_link_escapes_spaces():
    link = _rel_link("a.md", "my notes/b.md")
    assert "%20" in link


# ---------------------------------------------------------------------------
# build_suggestions — integration with temp vault
# ---------------------------------------------------------------------------

@pytest.fixture()
def simple_vault(tmp_path):
    """Create a minimal vault with 3 linked notes."""
    (tmp_path / "alpha.md").write_text(
        "---\ntitle: Alpha Guide\ntags: [guide, python]\n---\n\n# Alpha\nThis is about python programming and algorithms.\n",
        encoding="utf-8",
    )
    (tmp_path / "beta.md").write_text(
        "---\ntitle: Beta Reference\ntags: [reference, python]\n---\n\n# Beta\nPython reference for programming patterns and algorithms.\n",
        encoding="utf-8",
    )
    (tmp_path / "gamma.md").write_text(
        "---\ntitle: Gamma Cooking\ntags: [cooking, recipes]\n---\n\n# Gamma\nRecipes for pasta carbonara and risotto.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_build_suggestions_returns_all_docs(simple_vault):
    results = build_suggestions(simple_vault)
    assert len(results) == 3


def test_build_suggestions_alpha_related_to_beta(simple_vault):
    results = build_suggestions(simple_vault)
    alpha = next(r for r in results if "alpha" in r["path"])
    top_path = alpha["suggestions"][0]["path"]
    assert "beta" in top_path  # alpha and beta share python tags + similar content


def test_build_suggestions_gamma_lower_score(simple_vault):
    results = build_suggestions(simple_vault)
    alpha = next(r for r in results if "alpha" in r["path"])
    scores = {s["path"]: s["score"] for s in alpha["suggestions"]}
    beta_score = scores.get(next(k for k in scores if "beta" in k), 0)
    gamma_score = scores.get(next(k for k in scores if "gamma" in k), 0)
    assert beta_score > gamma_score


def test_build_suggestions_multi_vault(simple_vault, tmp_path):
    # Second vault with a single note — too few for standalone, but merged corpus works
    vault2 = tmp_path / "vault2"
    vault2.mkdir()
    (vault2 / "delta.md").write_text(
        "---\ntitle: Delta Python\ntags: [python]\n---\n\nMore python programming content.\n",
        encoding="utf-8",
    )
    results = build_suggestions([simple_vault, vault2])
    # Should have 4 docs total
    assert len(results) == 4
    # Paths from vault2 are prefixed with its name
    vault2_paths = [r["path"] for r in results if vault2.name in r["path"]]
    assert len(vault2_paths) == 1


def test_build_suggestions_exclude(simple_vault):
    results = build_suggestions(simple_vault, exclude=["gamma.md"])
    paths = [r["path"] for r in results]
    assert not any("gamma" in p for p in paths)


def test_build_suggestions_too_few_docs(tmp_path):
    (tmp_path / "only.md").write_text("# Solo\nOnly one file here.\n", encoding="utf-8")
    results = build_suggestions(tmp_path)
    assert results == []


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------

def test_parse_yaml_scalar():
    from marginalia.config import _parse_yaml
    d = _parse_yaml("min_score: 0.4\ntop_k: 10\n")
    assert d["min_score"] == 0.4
    assert d["top_k"] == 10


def test_parse_yaml_inline_list():
    from marginalia.config import _parse_yaml
    d = _parse_yaml("exclude: [node_modules/, .git/]\n")
    assert d["exclude"] == ["node_modules/", ".git/"]


def test_parse_yaml_block_list():
    from marginalia.config import _parse_yaml
    d = _parse_yaml("vaults:\n  - docs/\n  - ../wiki/\n")
    assert d["vaults"] == ["docs/", "../wiki/"]


def test_load_config_defaults():
    cfg = load_config(config_path="/nonexistent/path.yaml")
    assert cfg["min_score"] == 0.35
    assert cfg["top_k"] == 7


def test_load_config_from_file(tmp_path):
    cfg_file = tmp_path / "marginalia.yaml"
    cfg_file.write_text("min_score: 0.5\ntop_k: 15\nexclude:\n  - old/\n", encoding="utf-8")
    cfg = load_config(config_path=cfg_file)
    assert cfg["min_score"] == 0.5
    assert cfg["top_k"] == 15
    assert "old/" in cfg["exclude"]
