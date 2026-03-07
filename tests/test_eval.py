"""Tests for marginalia.eval — before/after RAG quality measurement."""

import json
import pytest
from pathlib import Path

from marginalia.eval import (
    _load_queries,
    _parse_queries_yaml,
    _query_corpus,
    _query_metrics,
    _summary_metrics,
    _build_corpus,
    run_snapshot,
    run_compare,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def rich_vault(tmp_path):
    """Vault with 5 notes covering different topics."""
    (tmp_path / "deploy.md").write_text(
        "---\ntitle: Deploy Guide\ntags: [ops, deployment]\n---\n\n"
        "How to deploy to production. Use git fetch and docker compose up. "
        "Never use SCP. Always fetch and reset hard on the server.\n",
        encoding="utf-8",
    )
    (tmp_path / "secrets.md").write_text(
        "---\ntitle: Secrets Management\ntags: [security, secrets]\n---\n\n"
        "PAT rotation and secrets governance. Store secrets in .env.secrets on server. "
        "Never hardcode credentials. Use Import-AgentSecrets at boot.\n",
        encoding="utf-8",
    )
    (tmp_path / "qdrant.md").write_text(
        "---\ntitle: Qdrant RAG Operations\ntags: [rag, qdrant, search]\n---\n\n"
        "Qdrant is used for semantic search. Collection easyway_wiki. "
        "Re-index with ingest_wiki.js. API key required.\n",
        encoding="utf-8",
    )
    (tmp_path / "agents.md").write_text(
        "---\ntitle: Agent Architecture\ntags: [agents, architecture]\n---\n\n"
        "L2 and L3 agents. Each agent needs manifest.json and README. "
        "Skills live in agents/skills/registry.json.\n",
        encoding="utf-8",
    )
    (tmp_path / "pr-workflow.md").write_text(
        "---\ntitle: PR Workflow\ntags: [git, pr, governance]\n---\n\n"
        "Pull request workflow. Palumbo rule: every PR needs a Work Item. "
        "No fast-forward merges. G12 branch policy.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def simple_queries(tmp_path):
    qfile = tmp_path / "queries.yaml"
    qfile.write_text(
        "queries:\n"
        "  - text: deploy to production server\n"
        "    expected:\n"
        "      - deploy.md\n"
        "  - text: secrets PAT rotation credentials\n"
        "    expected:\n"
        "      - secrets.md\n"
        "  - text: semantic search qdrant collection\n"
        "  - text: pull request work item governance\n"
        "    expected:\n"
        "      - pr-workflow.md\n",
        encoding="utf-8",
    )
    return qfile


# ---------------------------------------------------------------------------
# _load_queries
# ---------------------------------------------------------------------------

def test_load_queries_yaml(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text("queries:\n  - text: hello world\n  - text: another query\n", encoding="utf-8")
    queries = _load_queries(f)
    assert len(queries) == 2
    assert queries[0]["text"] == "hello world"
    assert queries[0]["expected"] == []


def test_load_queries_yaml_with_expected(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text(
        "queries:\n  - text: deploy guide\n    expected:\n      - deploy.md\n      - ops.md\n",
        encoding="utf-8",
    )
    queries = _load_queries(f)
    assert queries[0]["expected"] == ["deploy.md", "ops.md"]


def test_load_queries_yaml_inline_expected(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text('queries:\n  - text: foo bar\n    expected: ["a.md", "b.md"]\n', encoding="utf-8")
    queries = _load_queries(f)
    assert queries[0]["expected"] == ["a.md", "b.md"]


def test_load_queries_json_list(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps([{"text": "hello"}, {"text": "world", "expected": ["x.md"]}]), encoding="utf-8")
    queries = _load_queries(f)
    assert len(queries) == 2
    assert queries[1]["expected"] == ["x.md"]


def test_load_queries_json_dict(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"queries": [{"text": "test query"}]}), encoding="utf-8")
    queries = _load_queries(f)
    assert len(queries) == 1


def test_load_queries_missing_file():
    with pytest.raises(FileNotFoundError):
        _load_queries("/nonexistent/queries.yaml")


# ---------------------------------------------------------------------------
# _build_corpus + _query_corpus
# ---------------------------------------------------------------------------

def test_build_corpus_returns_docs(rich_vault):
    corpus = _build_corpus([rich_vault])
    assert len(corpus) == 5
    assert all("vec" in doc for doc in corpus)
    assert all("norm" in doc for doc in corpus)


def test_query_corpus_deploy(rich_vault):
    corpus = _build_corpus([rich_vault])
    results = _query_corpus("deploy production server", corpus, top_k=3, min_score=0.01)
    assert len(results) > 0
    paths = [r["path"] for r in results]
    assert any("deploy" in p for p in paths)


def test_query_corpus_secrets(rich_vault):
    corpus = _build_corpus([rich_vault])
    results = _query_corpus("secrets credentials PAT", corpus, top_k=3, min_score=0.01)
    assert len(results) > 0
    assert any("secrets" in r["path"] for r in results)


def test_query_corpus_empty_query(rich_vault):
    corpus = _build_corpus([rich_vault])
    results = _query_corpus("", corpus, top_k=3, min_score=0.0)
    assert results == []


def test_query_corpus_min_score_filters(rich_vault):
    corpus = _build_corpus([rich_vault])
    results_low = _query_corpus("deploy", corpus, top_k=5, min_score=0.0)
    results_high = _query_corpus("deploy", corpus, top_k=5, min_score=0.9)
    assert len(results_low) >= len(results_high)


# ---------------------------------------------------------------------------
# _query_metrics
# ---------------------------------------------------------------------------

def test_query_metrics_no_expected():
    results = [{"path": "a.md", "score": 0.8}, {"path": "b.md", "score": 0.6}]
    m = _query_metrics(results, [], top_k=5, min_score=0.1)
    assert m["top1_score"] == 0.8
    assert m["found"] is True
    assert "precision_at_k" not in m


def test_query_metrics_with_expected_hit():
    results = [{"path": "deploy.md", "score": 0.9}]
    m = _query_metrics(results, ["deploy.md"], top_k=5, min_score=0.1)
    assert m["precision_at_k"] == 1.0
    assert m["recall_at_k"] == 1.0


def test_query_metrics_with_expected_miss():
    results = [{"path": "other.md", "score": 0.9}]
    m = _query_metrics(results, ["deploy.md"], top_k=5, min_score=0.1)
    assert m["precision_at_k"] == 0.0
    assert m["recall_at_k"] == 0.0


def test_query_metrics_empty_results():
    m = _query_metrics([], ["deploy.md"], top_k=5, min_score=0.1)
    assert m["found"] is False
    assert m["top1_score"] == 0.0


# ---------------------------------------------------------------------------
# run_snapshot
# ---------------------------------------------------------------------------

def test_run_snapshot_creates_file(rich_vault, simple_queries, tmp_path):
    out = tmp_path / "snapshot.json"
    result = run_snapshot(rich_vault, simple_queries, out, top_k=3, min_score=0.05)
    assert out.exists()
    assert result["docs"] == 5
    assert result["summary"]["queries"] == 4
    assert 0.0 <= result["summary"]["coverage"] <= 1.0


def test_run_snapshot_structure(rich_vault, simple_queries, tmp_path):
    out = tmp_path / "snap.json"
    result = run_snapshot(rich_vault, simple_queries, out)
    assert "version" in result
    assert "createdAt" in result
    assert "queries" in result
    assert len(result["queries"]) == 4
    for q in result["queries"]:
        assert "text" in q
        assert "results" in q
        assert "metrics" in q


def test_run_snapshot_deploy_query_finds_deploy(rich_vault, simple_queries, tmp_path):
    out = tmp_path / "snap.json"
    result = run_snapshot(rich_vault, simple_queries, out, top_k=5, min_score=0.01)
    deploy_q = next(q for q in result["queries"] if "deploy" in q["text"])
    assert deploy_q["metrics"]["found"] is True
    # deploy.md should be in top results
    paths = [r["path"] for r in deploy_q["results"]]
    assert any("deploy" in p for p in paths)


def test_run_snapshot_precision_recall_computed(rich_vault, simple_queries, tmp_path):
    out = tmp_path / "snap.json"
    result = run_snapshot(rich_vault, simple_queries, out, top_k=5, min_score=0.01)
    # 3 of 4 queries have expected — summary should have precision/recall
    assert "precision_at_k" in result["summary"]


def test_run_snapshot_invalid_queries(rich_vault, tmp_path):
    bad_file = tmp_path / "empty.yaml"
    bad_file.write_text("queries:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No queries"):
        run_snapshot(rich_vault, bad_file, tmp_path / "out.json")


# ---------------------------------------------------------------------------
# run_compare
# ---------------------------------------------------------------------------

def test_run_compare_basic(rich_vault, simple_queries, tmp_path):
    snap_a = tmp_path / "before.json"
    snap_b = tmp_path / "after.json"

    run_snapshot(rich_vault, simple_queries, snap_a, top_k=3)

    # Simulate "after": add a new note to vault that matches deploy query better
    (rich_vault / "deploy-guide-v2.md").write_text(
        "---\ntitle: Deploy Guide v2\ntags: [ops, deployment, production]\n---\n\n"
        "Production deploy steps: fetch origin main, reset hard, docker compose up. "
        "Deploy deploy deploy server production.\n",
        encoding="utf-8",
    )
    run_snapshot(rich_vault, simple_queries, snap_b, top_k=3)

    result = run_compare(snap_a, snap_b)
    assert result["aggregate"]["queries_compared"] == 4
    assert "verdict" in result["aggregate"]
    assert len(result["queries"]) == 4


def test_run_compare_verdict_improved(tmp_path):
    before = {
        "version": 1, "createdAt": "2026-01-01T00:00:00Z", "docs": 5,
        "summary": {"queries": 2, "avg_top1_score": 0.30, "avg_mean_score": 0.25,
                    "coverage": 0.5, "queries_with_results": 1},
        "queries": [
            {"text": "deploy", "expected": [], "results": [{"path": "a.md", "score": 0.4}],
             "metrics": {"top1_score": 0.40, "mean_score": 0.40, "results_count": 1, "found": True}},
            {"text": "secrets", "expected": [], "results": [],
             "metrics": {"top1_score": 0.0, "mean_score": 0.0, "results_count": 0, "found": False}},
        ],
    }
    after = {
        "version": 1, "createdAt": "2026-01-02T00:00:00Z", "docs": 6,
        "summary": {"queries": 2, "avg_top1_score": 0.70, "avg_mean_score": 0.65,
                    "coverage": 1.0, "queries_with_results": 2},
        "queries": [
            {"text": "deploy", "expected": [], "results": [{"path": "a.md", "score": 0.8}],
             "metrics": {"top1_score": 0.80, "mean_score": 0.80, "results_count": 1, "found": True}},
            {"text": "secrets", "expected": [], "results": [{"path": "s.md", "score": 0.6}],
             "metrics": {"top1_score": 0.60, "mean_score": 0.60, "results_count": 1, "found": True}},
        ],
    }
    bf = tmp_path / "before.json"
    af = tmp_path / "after.json"
    bf.write_text(json.dumps(before), encoding="utf-8")
    af.write_text(json.dumps(after), encoding="utf-8")

    result = run_compare(bf, af)
    assert result["aggregate"]["verdict"] == "IMPROVED"
    assert result["aggregate"]["avg_top1_score_delta"] > 0


def test_run_compare_new_results_tracked(tmp_path):
    before = {
        "version": 1, "createdAt": "2026-01-01T00:00:00Z", "docs": 3,
        "summary": {"queries": 1, "avg_top1_score": 0.5, "avg_mean_score": 0.5, "coverage": 1.0, "queries_with_results": 1},
        "queries": [
            {"text": "test", "expected": [], "results": [{"path": "a.md", "score": 0.5}],
             "metrics": {"top1_score": 0.5, "mean_score": 0.5, "results_count": 1, "found": True}},
        ],
    }
    after = {
        "version": 1, "createdAt": "2026-01-02T00:00:00Z", "docs": 4,
        "summary": {"queries": 1, "avg_top1_score": 0.55, "avg_mean_score": 0.55, "coverage": 1.0, "queries_with_results": 1},
        "queries": [
            {"text": "test", "expected": [], "results": [{"path": "a.md", "score": 0.5}, {"path": "b.md", "score": 0.6}],
             "metrics": {"top1_score": 0.6, "mean_score": 0.55, "results_count": 2, "found": True}},
        ],
    }
    bf = tmp_path / "b.json"
    af = tmp_path / "a.json"
    bf.write_text(json.dumps(before), encoding="utf-8")
    af.write_text(json.dumps(after), encoding="utf-8")

    result = run_compare(bf, af)
    delta = result["queries"][0]
    assert "b.md" in delta["new_results"]
    assert delta["results_count_delta"] == 1
