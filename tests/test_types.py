"""Tests for marginalia.types — doc placement taxonomy.

PBI #1858: Marginalia types module + Cartografo --runbooks: doc placement
enforcement bottom-up. Mirrors the pattern of tags.py.
"""

import tempfile
from pathlib import Path

from marginalia.types import (
    DEFAULT_TYPES,
    load_types_taxonomy,
    discover_misplaced,
    add_type_to_frontmatter,
    fix_placement,
    summarize,
    _infer_type_from_path,
    _expected_path,
)


def _mk_vault(files):
    """files: {rel_path: content} -> temp vault dir."""
    tmp = tempfile.mkdtemp()
    base = Path(tmp)
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return base


def _fm(type_value=None, **kwargs):
    """Build a markdown file with frontmatter."""
    lines = ["---"]
    if type_value is not None:
        lines.append(f"type: {type_value}")
    for k, v in kwargs.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append("# body")
    lines.append("")
    return "\n".join(lines)


class TestDefaults:
    def test_default_types_has_canonical_folders(self):
        assert DEFAULT_TYPES["runbook"] == "Runbooks/"
        assert DEFAULT_TYPES["profile"] == "profiles/"
        assert DEFAULT_TYPES["feedback"] == "feedback/"
        assert DEFAULT_TYPES["governance"] == "guides/governance/"
        assert DEFAULT_TYPES["guide"] == "guides/"

    def test_load_taxonomy_no_yaml_returns_defaults(self):
        types = load_types_taxonomy(None)
        assert types == DEFAULT_TYPES

    def test_load_taxonomy_missing_yaml_returns_defaults(self):
        types = load_types_taxonomy("/nonexistent/path.yml")
        assert types == DEFAULT_TYPES

    def test_load_taxonomy_yaml_overrides_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False, encoding="utf-8") as f:
            f.write("types:\n  runbook: ops-runbooks/\n  custom: misc/x/\n")
            yml_path = f.name
        types = load_types_taxonomy(yml_path)
        assert types["runbook"] == "ops-runbooks/"
        assert types["custom"] == "misc/x/"


class TestInferTypeFromPath:
    def test_runbooks_folder(self):
        assert _infer_type_from_path("Runbooks/foo.md") == "runbook"

    def test_profiles_folder(self):
        assert _infer_type_from_path("profiles/founder.md") == "profile"

    def test_feedback_folder(self):
        assert _infer_type_from_path("feedback/realismo.md") == "feedback"

    def test_governance_specific_before_guide(self):
        assert _infer_type_from_path("guides/governance/handoff.md") == "governance"

    def test_vision_specific_before_guide(self):
        assert _infer_type_from_path("guides/vision/factory.md") == "vision"

    def test_lessons_specific_file(self):
        assert _infer_type_from_path("guides/lessons-learned.md") == "lessons"

    def test_chronicles_folder(self):
        assert _infer_type_from_path("chronicles/2026-04.md") == "chronicle"

    def test_guides_catchall(self):
        assert _infer_type_from_path("guides/random-guide.md") == "guide"

    def test_unknown_path_returns_none(self):
        assert _infer_type_from_path("misc/foo.md") is None

    def test_windows_path_separator_normalized(self):
        assert _infer_type_from_path("Runbooks\\foo.md") == "runbook"


class TestExpectedPath:
    def test_runbook_in_runbooks_folder(self):
        assert _expected_path("runbook", DEFAULT_TYPES, "foo.md") == "Runbooks/foo.md"

    def test_lessons_canonical_file(self):
        # lessons points to a single canonical file regardless of basename
        assert _expected_path("lessons", DEFAULT_TYPES, "anything.md") == "guides/lessons-learned.md"

    def test_unknown_type_returns_none(self):
        assert _expected_path("nonexistent-kind", DEFAULT_TYPES, "foo.md") is None


class TestDiscoverMisplaced:
    def test_runbook_in_guides_is_mismatch(self):
        vault = _mk_vault({
            "guides/backup-script.md": _fm(type_value="runbook"),
        })
        results = discover_misplaced(vault)
        assert len(results) == 1
        r = results[0]
        assert r["status"] == "placement_mismatch"
        assert r["declared_type"] == "runbook"
        assert r["expected_path"] == "Runbooks/backup-script.md"

    def test_correctly_placed_runbook_is_ok(self):
        vault = _mk_vault({
            "Runbooks/backup-script.md": _fm(type_value="runbook"),
        })
        results = discover_misplaced(vault)
        assert results[0]["status"] == "ok"

    def test_missing_type_inferable_from_path(self):
        vault = _mk_vault({
            "Runbooks/no-type.md": _fm(title="No Type"),
        })
        results = discover_misplaced(vault)
        r = results[0]
        assert r["status"] == "missing_type"
        assert r["declared_type"] is None
        assert r["inferred_type"] == "runbook"
        assert r["expected_path"] == "Runbooks/no-type.md"

    def test_no_frontmatter_at_all(self):
        vault = _mk_vault({
            "guides/raw.md": "# just a heading\n\nbody\n",
        })
        results = discover_misplaced(vault)
        r = results[0]
        assert r["status"] == "no_frontmatter"
        assert r["inferred_type"] == "guide"

    def test_unknown_type(self):
        vault = _mk_vault({
            "guides/x.md": _fm(type_value="nonexistent-kind"),
        })
        results = discover_misplaced(vault)
        r = results[0]
        assert r["status"] == "unknown"
        assert r["declared_type"] == "nonexistent-kind"
        assert r["expected_path"] is None

    def test_multiple_files_mixed_statuses(self):
        vault = _mk_vault({
            "Runbooks/ok.md":               _fm(type_value="runbook"),
            "guides/misplaced-runbook.md":  _fm(type_value="runbook"),
            "guides/no-type.md":            _fm(title="x"),
            "guides/raw.md":                "# raw\n",
            "guides/strange.md":            _fm(type_value="weirdo"),
        })
        results = discover_misplaced(vault)
        counts = summarize(results)
        assert counts.get("ok", 0) == 1
        assert counts.get("placement_mismatch", 0) == 1
        assert counts.get("missing_type", 0) == 1
        assert counts.get("no_frontmatter", 0) == 1
        assert counts.get("unknown", 0) == 1


class TestAddTypeToFrontmatter:
    def test_adds_to_existing_frontmatter(self):
        vault = _mk_vault({
            "guides/x.md": "---\ntitle: Foo\n---\n\nbody\n",
        })
        f = vault / "guides/x.md"
        assert add_type_to_frontmatter(f, "guide", dry_run=False) is True
        content = f.read_text(encoding="utf-8")
        assert "type: guide" in content
        assert "title: Foo" in content

    def test_creates_frontmatter_if_missing(self):
        vault = _mk_vault({
            "guides/x.md": "# heading\nbody\n",
        })
        f = vault / "guides/x.md"
        assert add_type_to_frontmatter(f, "guide", dry_run=False) is True
        content = f.read_text(encoding="utf-8")
        assert content.startswith("---\ntype: guide\n---\n")

    def test_dry_run_does_not_write(self):
        vault = _mk_vault({
            "guides/x.md": "---\ntitle: Foo\n---\nbody\n",
        })
        f = vault / "guides/x.md"
        original = f.read_text(encoding="utf-8")
        assert add_type_to_frontmatter(f, "guide", dry_run=True) is True
        assert f.read_text(encoding="utf-8") == original

    def test_skips_if_type_already_present(self):
        vault = _mk_vault({
            "guides/x.md": "---\ntype: guide\n---\nbody\n",
        })
        f = vault / "guides/x.md"
        assert add_type_to_frontmatter(f, "runbook", dry_run=False) is False
        # File should be unchanged
        assert "type: guide" in f.read_text(encoding="utf-8")
        assert "type: runbook" not in f.read_text(encoding="utf-8")


class TestFixPlacement:
    def test_dry_run_does_not_move(self):
        vault = _mk_vault({
            "guides/x.md": _fm(type_value="runbook"),
        })
        result = fix_placement(vault, "guides/x.md", "Runbooks/x.md", dry_run=True)
        assert result["action"] == "would_move"
        assert (vault / "guides/x.md").exists()
        assert not (vault / "Runbooks/x.md").exists()

    def test_apply_moves_file_no_git(self):
        vault = _mk_vault({
            "guides/x.md": _fm(type_value="runbook"),
        })
        result = fix_placement(vault, "guides/x.md", "Runbooks/x.md",
                               dry_run=False, use_git=False)
        assert result["action"] == "moved_fs"
        assert not (vault / "guides/x.md").exists()
        assert (vault / "Runbooks/x.md").exists()

    def test_skip_if_dest_exists(self):
        vault = _mk_vault({
            "guides/x.md":   "x",
            "Runbooks/x.md": "y",
        })
        result = fix_placement(vault, "guides/x.md", "Runbooks/x.md",
                               dry_run=False, use_git=False)
        assert result["action"] == "skip"
        assert result["reason"] == "dest exists"

    def test_skip_if_source_missing(self):
        vault = _mk_vault({})
        result = fix_placement(vault, "guides/missing.md", "Runbooks/missing.md",
                               dry_run=False, use_git=False)
        assert result["action"] == "skip"
        assert result["reason"] == "source missing"

    def test_creates_parent_dir(self):
        vault = _mk_vault({
            "guides/x.md": _fm(type_value="runbook"),
        })
        # Runbooks/ does not yet exist
        result = fix_placement(vault, "guides/x.md", "Runbooks/nested/x.md",
                               dry_run=False, use_git=False)
        assert result["action"] == "moved_fs"
        assert (vault / "Runbooks/nested/x.md").exists()


class TestSummarize:
    def test_counts_by_status(self):
        results = [
            {"status": "placement_mismatch"},
            {"status": "placement_mismatch"},
            {"status": "ok"},
            {"status": "no_frontmatter"},
        ]
        counts = summarize(results)
        assert counts["placement_mismatch"] == 2
        assert counts["ok"] == 1
        assert counts["no_frontmatter"] == 1
