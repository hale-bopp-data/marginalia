"""Tests for marginalia.schema — Karpathy LLM-Wiki blueprint (AB#2188)."""

from marginalia.schema import (
    PATH_FIELDS,
    SCHEMA_FILENAME,
    find_schema,
    init_schema,
    parse_schema,
    render_default_template,
    show_schema,
    validate_schema,
)


def _make_page(path, title="Page", tags=None, type_="entity", body="Body."):
    tags = tags or ["domain/test"]
    fm = (
        "---\n"
        f"title: {title}\n"
        f"tags: {tags}\n"
        f"type: {type_}\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm, encoding="utf-8")


def test_render_template_contains_required_yaml_keys():
    out = render_default_template("my-vault")
    for key in ("title:", "type:", "vault_purpose:", "entity_paths:", "raw_paths:",
                "required_frontmatter:", "page_types:", "tag_taxonomy_ref:"):
        assert key in out, f"missing template key: {key}"
    assert "my-vault" in out


def test_init_creates_schema(tmp_path):
    result = init_schema(tmp_path)
    assert result["action"] == "created"
    assert (tmp_path / SCHEMA_FILENAME).is_file()


def test_init_refuses_overwrite_without_force(tmp_path):
    init_schema(tmp_path)
    result = init_schema(tmp_path, force=False)
    assert result["action"] == "skipped"
    assert "already exists" in result["reason"]


def test_init_force_overwrites(tmp_path):
    init_schema(tmp_path)
    (tmp_path / SCHEMA_FILENAME).write_text("---\ntitle: stale\n---\nold body", encoding="utf-8")
    result = init_schema(tmp_path, force=True)
    assert result["action"] in ("created", "overwritten")
    content = (tmp_path / SCHEMA_FILENAME).read_text(encoding="utf-8")
    assert "Karpathy" in content


def test_find_schema_returns_none_when_missing(tmp_path):
    assert find_schema(tmp_path) is None


def test_find_schema_returns_path_when_present(tmp_path):
    init_schema(tmp_path)
    found = find_schema(tmp_path)
    assert found is not None
    assert found.name == SCHEMA_FILENAME


def test_parse_schema_extracts_frontmatter_and_body(tmp_path):
    init_schema(tmp_path)
    parsed = parse_schema(tmp_path / SCHEMA_FILENAME)
    assert parsed["frontmatter"]["title"] == "Wiki Schema"
    assert parsed["frontmatter"]["type"] == "schema"
    for field in PATH_FIELDS:
        assert isinstance(parsed["frontmatter"][field], list)
    assert "Wiki Schema" in parsed["body"]


def test_show_schema_missing(tmp_path):
    result = show_schema(tmp_path)
    assert result["status"] == "missing"


def test_show_schema_ok(tmp_path):
    init_schema(tmp_path)
    result = show_schema(tmp_path)
    assert result["status"] == "ok"
    assert result["frontmatter"]["type"] == "schema"


def test_validate_missing_schema(tmp_path):
    result = validate_schema(tmp_path)
    assert result["status"] == "missing"


def test_validate_reports_missing_directories(tmp_path):
    init_schema(tmp_path)
    # Template declares entities/, concepts/, synthesis/, raw/ — none exist
    result = validate_schema(tmp_path)
    assert result["status"] == "issues"
    failed = [c for c in result["checks"] if not c["passed"] and c["check"] == "path_exists"]
    assert len(failed) >= 4


def test_validate_ok_when_paths_and_frontmatter_complete(tmp_path):
    init_schema(tmp_path)
    # Create declared directories
    for d in ("entities", "concepts", "synthesis", "raw"):
        (tmp_path / d).mkdir()
    # Create taxonomy.yml referenced by schema
    (tmp_path / "taxonomy.yml").write_text("namespaces: {}\n", encoding="utf-8")
    # Add a compliant entity page
    _make_page(tmp_path / "entities" / "alpha.md", title="Alpha", type_="entity")
    result = validate_schema(tmp_path)
    assert result["status"] == "ok", f"expected ok, got: {result}"


def test_validate_skips_raw_paths_in_frontmatter_check(tmp_path):
    init_schema(tmp_path)
    for d in ("entities", "concepts", "synthesis", "raw"):
        (tmp_path / d).mkdir()
    (tmp_path / "taxonomy.yml").write_text("namespaces: {}\n", encoding="utf-8")
    # A raw page WITHOUT required frontmatter — should be excluded from check
    (tmp_path / "raw" / "transcript.md").write_text("# Raw transcript\nbody\n", encoding="utf-8")
    result = validate_schema(tmp_path)
    assert result["status"] == "ok"
    assert result["missing_frontmatter_files"] == {}


def test_validate_reports_missing_required_frontmatter(tmp_path):
    init_schema(tmp_path)
    for d in ("entities", "concepts", "synthesis", "raw"):
        (tmp_path / d).mkdir()
    (tmp_path / "taxonomy.yml").write_text("namespaces: {}\n", encoding="utf-8")
    # Page WITHOUT required `type` field
    bad = tmp_path / "entities" / "bad.md"
    bad.write_text("---\ntitle: Bad\ntags: [domain/test]\n---\n\nbody\n", encoding="utf-8")
    result = validate_schema(tmp_path)
    assert result["status"] == "issues"
    rel = bad.relative_to(tmp_path).as_posix()
    assert rel in result["missing_frontmatter_files"]
    assert "type" in result["missing_frontmatter_files"][rel]


def test_validate_detects_missing_taxonomy_ref(tmp_path):
    init_schema(tmp_path)
    for d in ("entities", "concepts", "synthesis", "raw"):
        (tmp_path / d).mkdir()
    # NO taxonomy.yml created
    result = validate_schema(tmp_path)
    tax_check = next((c for c in result["checks"] if c["check"] == "taxonomy_exists"), None)
    assert tax_check is not None
    assert tax_check["passed"] is False
