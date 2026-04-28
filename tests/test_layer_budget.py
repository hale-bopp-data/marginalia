"""Tests for layer budget enforcement (Giro 6 — Matrioska)."""

import pytest
from pathlib import Path

from marginalia.scanner import check_layer_budget


def _mk_content(lines):
    return "\n".join(lines)


class TestLayerBudget:
    def test_max_lines_violated(self):
        content = _mk_content(["# Title", "", "line1", "line2", "line3", "line4", "line5", "line6"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 5}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 1
        assert issues[0]["type"] == "layer_budget_exceeded"
        assert "L0" in issues[0]["description"]
        assert "5" in issues[0]["description"]

    def test_max_lines_within_budget(self):
        content = _mk_content(["# Title", "", "line1", "line2", "line3"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 10}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_pointer_density_below_minimum(self):
        content = _mk_content(["# Title", "", "text without links", "more text", "still no links"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"min_pointer_density": 0.4}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 1
        assert issues[0]["type"] == "layer_budget_exceeded"
        assert "pointer density" in issues[0]["description"]

    def test_pointer_density_above_minimum(self):
        content = _mk_content([
            "# Index", "",
            "- [[A]]", "- [[B]]", "- [[C]]",
            "- [D](d.md)", "- [E](e.md)",
            "some extra text",
        ])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"min_pointer_density": 0.3}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_both_max_lines_and_pointer_density_violated(self):
        content = _mk_content(["# Title"] + ["extra line" + str(i) for i in range(10)])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 5, "min_pointer_density": 0.5}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 2

    def test_no_layer_map_returns_empty(self):
        content = _mk_content(["# Title", "line1", "line2", "line3"])
        scfg = {"_layer_map": {}, "layer_budgets": {"L0": {"max_lines": 2}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_no_budgets_returns_empty(self):
        content = _mk_content(["# Title", "line1", "line2", "line3"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_file_not_in_layer_map_returns_empty(self):
        content = _mk_content(["# Title", "line1", "line2", "line3"])
        scfg = {"_layer_map": {"other.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 2}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_layer_not_in_budgets_returns_empty(self):
        content = _mk_content(["# Title", "line1", "line2", "line3"])
        scfg = {"_layer_map": {"test.md": "L2"}, "layer_budgets": {"L0": {"max_lines": 5}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_zero_max_lines_ignored(self):
        content = _mk_content(["# Title", "line1", "line2"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 0}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_negative_max_lines_ignored(self):
        content = _mk_content(["# Title", "line1", "line2"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": -1}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_min_pointer_density_zero_ignored(self):
        content = _mk_content(["# Title", "text", "more text"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"min_pointer_density": 0}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0

    def test_exact_max_lines_boundary(self):
        content = _mk_content(["line" + str(i) for i in range(5)])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": 5}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0  # 5 lines = 5 budget, no violation

    def test_string_max_lines_not_int(self):
        content = _mk_content(["# Title", "line1", "line2", "line3", "line4", "line5"])
        scfg = {"_layer_map": {"test.md": "L0"}, "layer_budgets": {"L0": {"max_lines": "5"}}}
        issues = check_layer_budget("test.md", content, scfg)
        assert len(issues) == 0  # string not coerced by check_layer_budget
