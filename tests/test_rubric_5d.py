"""Tests for 5-domande rubric — validators + scanner + CLI integration.

PBI #1801, founder voice S483, doctrine: wiki-frontmatter-schema.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marginalia.scanner import scan_file
from marginalia.validators import (
    RUBRIC_5D_PREDICATES,
    is_valid_purpose,
    is_valid_qa,
    is_valid_related,
    is_valid_when_to_use,
    is_valid_why,
    validate_5d_rubric,
)


# --- Field validator unit tests ---

def test_purpose_valid_string():
    assert is_valid_purpose({"purpose": "Hook PreToolUse che applica filtro G34."})


def test_purpose_too_short():
    assert not is_valid_purpose({"purpose": "ok"})


def test_purpose_placeholder_rejected():
    assert not is_valid_purpose({"purpose": "TODO"})
    assert not is_valid_purpose({"purpose": "tbd"})


def test_purpose_missing():
    assert not is_valid_purpose({})


def test_when_to_use_valid():
    assert is_valid_when_to_use({"when_to_use": "Quando un agente tenta mutation."})


def test_when_to_use_placeholder():
    assert not is_valid_when_to_use({"when_to_use": "..."})


def test_why_valid_with_origin():
    assert is_valid_why({"why": "G34 invariant. Originato S483 PBI #1780."})


def test_why_too_short():
    assert not is_valid_why({"why": "x"})


def test_qa_valid_list_of_dicts():
    fm = {"qa": [{"q": "How to unblock?", "a": "Run printf state file."}]}
    assert is_valid_qa(fm)


def test_qa_empty_list_permitted():
    assert is_valid_qa({"qa": []})


def test_qa_missing_keys_rejected():
    assert not is_valid_qa({"qa": [{"q": "no answer"}]})


def test_qa_missing_field():
    assert not is_valid_qa({})


def test_related_valid_list():
    assert is_valid_related({"related": ["guides/governance/foo.md", "lessons-learned.md"]})


def test_related_empty_list_permitted():
    assert is_valid_related({"related": []})


def test_related_missing():
    assert not is_valid_related({})


# --- Aggregator tests ---

def test_validate_5d_rubric_all_pass():
    fm = {
        "purpose": "Hook PreToolUse che applica filtro G34 5-domande.",
        "when_to_use": "Quando un agente tenta mutation post-15min senza re-validation.",
        "why": "G34 invariant. Originato S483 PBI #1780, hardened S492 Bug #1816.",
        "qa": [{"q": "How to unblock?", "a": "printf state file or SESSION_AUTHORIZED env."}],
        "related": ["guides/governance/agent-reasoning-checklist.md"],
    }
    report = validate_5d_rubric(fm)
    assert report["valid"] is True
    assert report["confidence"] == 1.0
    assert len(report["passed"]) == 5
    assert len(report["failed"]) == 0


def test_validate_5d_rubric_all_missing():
    report = validate_5d_rubric({})
    assert report["valid"] is False
    assert report["confidence"] == 0.0
    assert len(report["failed"]) == 5


def test_validate_5d_rubric_partial():
    fm = {
        "purpose": "Schema doc machine-checkable.",
        "when_to_use": "Quando crei pagina wiki.",
    }
    report = validate_5d_rubric(fm)
    assert report["valid"] is False
    assert len(report["passed"]) == 2
    assert len(report["failed"]) == 3
    failed_fields = {f["field"] for f in report["failed"]}
    assert failed_fields == {"why", "qa", "related"}


def test_predicates_have_field_metadata():
    for pred in RUBRIC_5D_PREDICATES:
        assert "id" in pred
        assert "field" in pred
        assert "description" in pred
        assert "check" in pred


# --- Scanner integration ---

@pytest.fixture
def vault_with_5d_doc(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "compliant.md"
    doc.write_text(
        """---
title: "Test doc"
purpose: "Verifica scanner integration con rubric 5d."
when_to_use: "Test pytest CI per regressione PBI #1801."
why: "S492 PBI #1801 — coverage AC2 scanner integration."
qa:
  - q: "Test passa?"
    a: "Si, verifica predicate engine."
related:
  - other.md
tags: [test, pbi-1801]
status: active
---

# Compliant doc
""",
        encoding="utf-8",
    )
    return vault


def test_scanner_no_5d_issue_when_disabled(vault_with_5d_doc):
    """When validate_5d_rubric=False (default), no missing_5d_field issues raised."""
    scfg = {"validate_5d_rubric": False, "valid_statuses": ["active"]}
    issues = scan_file(
        vault_with_5d_doc / "compliant.md",
        vault_with_5d_doc,
        scanner_config=scfg,
    )
    types = {i["type"] for i in issues}
    assert "missing_5d_field" not in types


def test_scanner_compliant_passes(vault_with_5d_doc):
    """Compliant doc with all 5 fields has no missing_5d_field issues."""
    scfg = {"validate_5d_rubric": True, "valid_statuses": ["active"]}
    issues = scan_file(
        vault_with_5d_doc / "compliant.md",
        vault_with_5d_doc,
        scanner_config=scfg,
    )
    missing_5d = [i for i in issues if i["type"] == "missing_5d_field"]
    assert missing_5d == []


def test_scanner_non_compliant_flags_all_5(tmp_path: Path):
    """Doc missing all 5 fields → 5 missing_5d_field issues."""
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "missing.md"
    doc.write_text(
        """---
title: "Test doc"
tags: [test]
status: active
---

# No 5-domande fields
""",
        encoding="utf-8",
    )
    scfg = {"validate_5d_rubric": True, "valid_statuses": ["active"]}
    issues = scan_file(doc, vault, scanner_config=scfg)
    missing_5d = [i for i in issues if i["type"] == "missing_5d_field"]
    assert len(missing_5d) == 5
    fields = {f"{i['description']}".split("field: ")[1].rstrip(")") for i in missing_5d}
    assert fields == {"purpose", "when_to_use", "why", "qa", "related"}


def test_scanner_template_excluded(tmp_path: Path):
    """Templates do not trigger 5d rubric checks (consistent with other checks)."""
    vault = tmp_path / "vault"
    (vault / "templates").mkdir(parents=True)
    doc = vault / "templates" / "guide-template.md"
    doc.write_text(
        """---
title: "Template"
tags: [template]
---
# Template
""",
        encoding="utf-8",
    )
    scfg = {"validate_5d_rubric": True}
    issues = scan_file(doc, vault, scanner_config=scfg)
    missing_5d = [i for i in issues if i["type"] == "missing_5d_field"]
    assert missing_5d == []
