"""Handoff v2 declarative structure validator.

Checks handoff files against the 9-section canonical format:
  1. ISTRUZIONE
  2. PRE-FLIGHT
  3. STATO
  4. TASK PRIMARIO
  5. TASK SECONDARI
  6. PARKING-LOT
  7. VINCOLI ASSOLUTI
  8. STOP CONDITIONS
  9. DEFINITION OF DONE

Also checks validation checklist completeness (10 items).
"""

from __future__ import annotations

import re
from pathlib import Path

HANDOFF_SECTIONS = [
    (1, "ISTRUZIONE"),
    (2, "PRE-FLIGHT"),
    (3, "STATO"),
    (4, "TASK PRIMARIO"),
    (5, "TASK SECONDARI"),
    (6, "PARKING-LOT"),
    (7, "VINCOLI ASSOLUTI"),
    (8, "STOP CONDITIONS"),
    (9, "DEFINITION OF DONE"),
]


def _extract_section(content, heading_text):
    """Extract content between a heading and the next heading of same or higher level."""
    pattern = rf"^##\s+\d+\.?\s*{re.escape(heading_text)}.*?\n(.*?)(?=^##\s+|^#\s+[^#]|\Z)"
    m = re.search(pattern, content, re.DOTALL | re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip()


def _has_yaml_block(text):
    """Check if text contains a valid-looking YAML code block."""
    blocks = re.findall(r"```(?:yaml)?\s*\n(.*?)```", text, re.DOTALL)
    for block in blocks:
        if re.search(r"^\s*-?\s*(?:name|verify|task|action):", block, re.MULTILINE):
            return True
    return False


def _check_parking_triggers(content):
    """Check that parking-lot entries have binary/date triggers, not arbitrary dates."""
    section = _extract_section(content, "PARKING-LOT")
    if not section:
        return True, "Section not found"

    # Each parking entry should be numbered like "6.XXX — description"
    entries = re.findall(r"-\s+\*\*[\d.]+\*\*\s*[-—]\s*(.+)", section)
    if not entries:
        return True, "No parking entries found"

    issues = []
    for entry in entries:
        has_date = bool(re.search(r"\b\d{4}-\d{2}-\d{2}\b", entry))
        has_trigger = bool(re.search(r"(?:trigger|quando\s+[A-Z]|se\s+[A-Z]|dopo\s+[A-Z]|al\s+prossimo)\b", entry.lower()))
        if has_date and not has_trigger:
            issues.append(entry[:80])

    return len(issues) == 0, issues


def _check_checkpoint_format(content):
    """Check that [CHECKPOINT SNNN] markers are well-formed."""
    checkpoints = re.findall(r"\[CHECKPOINT\s+[^\]]+\]", content)
    bad = []
    for cp in checkpoints:
        if not re.match(r"^\[CHECKPOINT\s+S\d+\]$", cp):
            bad.append(cp)
    return len(bad) == 0, bad


def _check_v1_anti_patterns(content):
    """Detect v1-style hardcoded lists instead of v2 declarative YAML."""
    section = _extract_section(content, "TASK PRIMARIO")
    if not section:
        return True, []

    # v1 pattern: numbered list "1. Do X" with no YAML verify block
    has_yaml = _has_yaml_block(section)
    has_numbered = bool(re.search(r"^\s*\d+\.\s+\S", section, re.MULTILINE))
    if has_numbered and not has_yaml:
        return False, ["Numbered list without YAML verify blocks (v1 pattern)"]
    return True, []


def validate_handoff(filepath):
    """Validate a handoff file against the v2 9-section format.

    Returns dict with:
        valid: bool
        errors: list of {section, reason}
        warnings: list of {section, detail}
        sections_found: list of section names present
    """
    path = Path(filepath)
    if not path.is_file():
        return {"valid": False, "errors": [{"section": 0, "reason": "File not found"}],
                "warnings": [], "sections_found": []}

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"valid": False, "errors": [{"section": 0, "reason": f"Read error: {e}"}],
                "warnings": [], "sections_found": []}

    errors = []
    warnings = []
    sections_found = []

    # 1. Check all 9 sections
    for num, name in HANDOFF_SECTIONS:
        section = _extract_section(content, name)
        if section is None:
            errors.append({"section": num, "reason": f"Missing section: {name}"})
        elif len(section) < 10:
            errors.append({"section": num, "reason": f"Section empty or too short: {name}"})
        else:
            sections_found.append(name)

    # 2. Check YAML verify blocks in PRE-FLIGHT and STATO
    for sec_name in ("PRE-FLIGHT", "STATO"):
        section = _extract_section(content, sec_name)
        if section and not _has_yaml_block(section):
            warnings.append({"section": sec_name, "detail": "No YAML verify block found (v2 declarative pattern)"})

    # 3. Check parking-lot triggers
    parking_ok, parking_issues = _check_parking_triggers(content)
    if not parking_ok:
        for issue in parking_issues:
            warnings.append({"section": "PARKING-LOT",
                             "detail": f"Entry with date but no binary trigger: {issue}"})

    # 4. Check checkpoint format
    cp_ok, cp_bad = _check_checkpoint_format(content)
    if not cp_ok:
        for cp in cp_bad[:5]:
            errors.append({"section": 0, "reason": f"Malformed checkpoint: {cp}"})

    # 5. Check v1 anti-patterns
    v1_ok, v1_warnings = _check_v1_anti_patterns(content)
    if not v1_ok:
        for w in v1_warnings:
            warnings.append({"section": "TASK PRIMARIO", "detail": w})

    # 6. Check validation checklist
    checklist_match = re.search(r"## VALIDATION CHECKLIST\s*\n(.*?)(?=^##|\Z)", content, re.DOTALL | re.MULTILINE)
    if checklist_match:
        items = re.findall(r"-\s*\[([ xX])\]\s*(.*)", checklist_match.group(1))
        checked = sum(1 for c, _ in items if c.strip().lower() == "x")
        if len(items) < 8:
            warnings.append({"section": "VALIDATION CHECKLIST",
                             "detail": f"Only {len(items)} checklist items (expected 10)"})
        if checked < len(items) and len(items) >= 8:
            warnings.append({"section": "VALIDATION CHECKLIST",
                             "detail": f"Only {checked}/{len(items)} items checked"})
    else:
        warnings.append({"section": "VALIDATION CHECKLIST", "detail": "Missing validation checklist"})

    valid = len(errors) == 0
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "sections_found": sections_found,
        "sections_expected": len(HANDOFF_SECTIONS),
    }
