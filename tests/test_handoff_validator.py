"""Tests for handoff v2 structure validator."""

import pytest
from pathlib import Path
from marginalia.handoff_validator import validate_handoff


def _tmp_handoff(tmp_path, content):
    p = tmp_path / "handoff.md"
    p.write_text(content, encoding="utf-8")
    return p


class TestHandoffValidator:
    def test_valid_full_handoff(self, tmp_path):
        content = """\
# Handoff S1 -> S2

## 1. ISTRUZIONE
Bootstrap instructions here.

## 2. PRE-FLIGHT
```yaml
- name: "Check health"
  verify_command: "health.sh"
  expected: exit 0
```
More pre-flight text.

## 3. STATO
```yaml
- name: "PR #123"
  verify: ado_pr_get id=123
  expected: completed
```
Status details.

## 4. TASK PRIMARIO
```yaml
task: closeout
actions:
  1. Do thing
estimate: 1h
```

## 5. TASK SECONDARI
Secondary tasks list here with enough text.

## 6. PARKING-LOT
- **6.1** — Description with trigger keyword

## 7. VINCOLI ASSOLUTI
Absolute constraints section. With enough detail to pass.

## 8. STOP CONDITIONS
Stop conditions with specific blast radius.

## 9. DEFINITION OF DONE
Definition of done with binary criteria.

## VALIDATION CHECKLIST
- [x] Item 1
- [x] Item 2
- [x] Item 3
- [x] Item 4
- [x] Item 5
- [x] Item 6
- [x] Item 7
- [x] Item 8
- [ ] Item 9
- [ ] Item 10
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert report["valid"]
        assert len(report["sections_found"]) == 9

    def test_missing_section(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap.

## 2. PRE-FLIGHT
Checks.

## 3. STATO
Status.

## 4. TASK PRIMARIO
Task.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert not report["valid"]
        assert len(report["errors"]) >= 5  # missing sections 5-9

    def test_empty_section(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap.

## 2. PRE-FLIGHT
Pre-flight check.

## 3. STATO
Current state information here.

## 4. TASK PRIMARIO
### Short
## 5. TASK SECONDARI
Second task list.

## 6. PARKING-LOT
Parking lot entries here with enough detail to pass minimum length check.

## 7. VINCOLI ASSOLUTI
Long enough constraints text.

## 8. STOP CONDITIONS
Stop conditions section text.

## 9. DEFINITION OF DONE
Done definition content.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert not report["valid"]
        assert any("too short" in e["reason"] for e in report["errors"])

    def test_malformed_checkpoint(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap with [CHECKPOINT S1] and [CHECKPOINT bad] marker.

## 2. PRE-FLIGHT
Some pre-flight content here.

## 3. STATO
Current state goes here with details.

## 4. TASK PRIMARIO
Primary task description with enough content.

## 5. TASK SECONDARI
Second task list with enough details.

## 6. PARKING-LOT
Parking entries here for detail.

## 7. VINCOLI ASSOLUTI
Constraints section text here.

## 8. STOP CONDITIONS
Stop conditions with details.

## 9. DEFINITION OF DONE
Done criteria content.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert not report["valid"]
        assert any("bad" in e["reason"] for e in report["errors"])

    def test_file_not_found(self):
        report = validate_handoff("/nonexistent/path.md")
        assert not report["valid"]
        assert report["errors"][0]["reason"] == "File not found"

    def test_parking_binary_trigger_warning(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap.

## 2. PRE-FLIGHT
Some pre-flight go here text.

## 3. STATO
Status section with content.

## 4. TASK PRIMARIO
Primary task description.

## 5. TASK SECONDARI
Secondary tasks list content.

## 6. PARKING-LOT
- **6.1** — Something due 2026-04-28 for review
- **6.2** — Another item with binary trigger

## 7. VINCOLI ASSOLUTI
Constraints content.

## 8. STOP CONDITIONS
Stop conditions content.

## 9. DEFINITION OF DONE
Done criteria content.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert report["valid"]  # warnings only, not errors
        assert any("binary trigger" in w["detail"] for w in report["warnings"])

    def test_v1_anti_pattern_detection(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap stuff.

## 2. PRE-FLIGHT
More pre-flight content.

## 3. STATO
State information here.

## 4. TASK PRIMARIO
1. First thing to do
2. Second thing to do
3. Third thing to do

## 5. TASK SECONDARI
Secondaries content.

## 6. PARKING-LOT
Parking entries here.

## 7. VINCOLI ASSOLUTI
Constraints text.

## 8. STOP CONDITIONS
Stop conditions content.

## 9. DEFINITION OF DONE
Done criteria content.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        # Should warn about v1 numbered list without YAML
        assert any("v1 pattern" in w["detail"].lower() for w in report["warnings"])

    def test_missing_checklist_warning(self, tmp_path):
        content = """\
# Handoff

## 1. ISTRUZIONE
Bootstrap stuff.

## 2. PRE-FLIGHT
Pre-flight content here.

## 3. STATO
Status information here.

## 4. TASK PRIMARIO
Primary task description text here with enough length to pass validation.

## 5. TASK SECONDARI
Secondary tasks content text here with enough detail to pass validation.

## 6. PARKING-LOT
Parking entries content with enough detail.

## 7. VINCOLI ASSOLUTI
Constraints section content.

## 8. STOP CONDITIONS
Stop conditions content.

## 9. DEFINITION OF DONE
Done criteria content.
"""
        p = _tmp_handoff(tmp_path, content)
        report = validate_handoff(p)
        assert report["valid"]  # missing checklist is warning, not error
        assert any("checklist" in w["detail"].lower() for w in report["warnings"])
