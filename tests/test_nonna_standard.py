"""Tests for Nonna Standard compliance scanner (6 elementi strutturali)."""

from marginalia.scanner import check_nonna_standard


def _score(content):
    score, checks = check_nonna_standard(content, "test.md")
    return score, checks


class TestNonnaStandard:
    def test_full_compliance_score_6(self):
        content = """\
## Prerequisiti

| Campo | Valore |
|-------|--------|
| PATH | /opt/app |
| KEY | abc123 |

## Approccio

1. Primo metodo
2. Secondo metodo
3. Terzo metodo

```bash
echo recipe 1
```

```bash
echo recipe 2
```

```bash
echo recipe 3
```

## Troubleshooting

| Problema | Causa | Soluzione |
|----------|-------|-----------|
| Error 1 | Bug | Fix 1 |
| Error 2 | Bug | Fix 2 |

## Da directory esterne

Usare path assoluti.

## Riferimenti

- [Guida 1](guide1.md)
- [Guida 2](guide2.md)
"""
        score, checks = _score(content)
        assert score == 6, f"Expected 6, got {score}: {[c for c in checks if not c['passed']]}"

    def test_zero_elements(self):
        content = "# Just a title\n\nNo structure.\n"
        score, _ = _score(content)
        assert score == 0

    def test_table_detection(self):
        content = """\
| Col1 | Col2 |
|------|------|
| A | B |
| C | D |
"""
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "prereq_table")
        assert c["passed"]

    def test_no_table(self):
        content = "No tables here.\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "prereq_table")
        assert not c["passed"]

    def test_ordered_list_method(self):
        content = "1. First\n2. Second\n3. Third\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "method_approach")
        assert c["passed"]

    def test_few_ordered_items(self):
        content = "1. Only one\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "method_approach")
        assert not c["passed"]

    def test_three_code_blocks(self):
        content = "```a```\n```b```\n```c```\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "copy_paste_recipes")
        assert c["passed"]

    def test_two_code_blocks(self):
        content = "```a```\n```b```\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "copy_paste_recipes")
        assert not c["passed"]

    def test_troubleshooting_section(self):
        content = """\
## Troubleshooting

| Error | Fix |
|-------|-----|
| E1 | F1 |
| E2 | F2 |
"""
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "troubleshooting")
        assert c["passed"]

    def test_no_troubleshooting(self):
        content = "## FAQ\n\nJust text.\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "troubleshooting")
        assert not c["passed"]

    def test_external_dirs_section(self):
        content = "## Da directory esterne\n\nUse absolute paths.\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "external_dirs")
        assert c["passed"]

    def test_related_links(self):
        content = """\
## Riferimenti

- [Guide A](a.md)
- [[Guide B]]
"""
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "related_links")
        assert c["passed"]

    def test_vedi_anche_works_too(self):
        content = "## Vedi anche\n\n[Guide](g.md)\n"
        score, checks = _score(content)
        c = next(c for c in checks if c["name"] == "related_links")
        assert c["passed"]
