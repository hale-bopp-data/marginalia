"""Tests for operator-facing catalog and quickstart blueprint helpers."""

import json

from marginalia.operator import (
    build_quickstart_blueprint,
    get_catalog,
    materialize_quickstart,
)


def test_catalog_has_operator_entrypoints():
    catalog = get_catalog()
    ids = {item["id"] for item in catalog}
    assert "operator-quickstart" in ids
    assert "tag-catalog" in ids
    assert "link-materialization" in ids


def test_quickstart_blueprint_recommends_metadata_catalog(tmp_path):
    (tmp_path / "alpha.md").write_text(
        "---\n"
        "title: Alpha\n"
        "tags: [python]\n"
        "---\n\n"
        "# Alpha\n\n"
        "Python notes with no domain tag.\n",
        encoding="utf-8",
    )
    (tmp_path / "beta.md").write_text(
        "---\n"
        "title: Beta\n"
        "tags: [python]\n"
        "---\n\n"
        "# Beta\n\n"
        "More Python notes with no domain tag.\n",
        encoding="utf-8",
    )

    blueprint = build_quickstart_blueprint(tmp_path)

    assert blueprint["summary"]["files"] == 2
    assert blueprint["summary"]["flat_tags"] == 1
    assert blueprint["summary"]["scan_issues"] >= 2
    commands = [step["command"] for step in blueprint["recommended_flow"]]
    assert "marginalia tags <vault>" in commands
    assert any(issue["type"] in ("missing_domain_tag", "missing_required_tag") for issue in blueprint["top_issues"])


def test_quickstart_materialization_writes_json_and_markdown(tmp_path):
    (tmp_path / "note.md").write_text(
        "---\n"
        "title: Note\n"
        "tags: [domain/docs]\n"
        "---\n\n"
        "# Note\n\n"
        "Useful content.\n",
        encoding="utf-8",
    )

    blueprint = build_quickstart_blueprint(tmp_path)
    output = materialize_quickstart(blueprint, tmp_path / "out")

    json_path = tmp_path / "out" / "operator-blueprint.json"
    md_path = tmp_path / "out" / "operator-blueprint.md"

    assert output["json"].endswith("operator-blueprint.json")
    assert output["markdown"].endswith("operator-blueprint.md")
    assert json_path.exists()
    assert md_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["action"] == "marginalia-quickstart"
    assert "# marginalia Operator Blueprint" in md_path.read_text(encoding="utf-8")
