"""Unit tests for search_agent.py — keyword search and scope matching."""

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pytest

from search_agent import SearchAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo(tmp_path):
    """Create a small temporary repo structure for testing."""
    # Python file
    py_file = tmp_path / "main.py"
    py_file.write_text("def hello():\n    print('hello world')\n", encoding="utf-8")

    # Markdown doc
    md_file = tmp_path / "README.md"
    md_file.write_text("# Project\nThis is a sample project about machine learning.\n", encoding="utf-8")

    # Text file
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Notes about the quantum computing research project.\n", encoding="utf-8")

    # Non-matching file
    css_file = tmp_path / "style.css"
    css_file.write_text("body { color: red; }\n", encoding="utf-8")

    # Nested directory
    sub = tmp_path / "lib"
    sub.mkdir()
    sub_file = sub / "utils.py"
    sub_file.write_text("def compute():\n    return 42\n", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# _match_scope
# ---------------------------------------------------------------------------

class TestMatchScope:
    def test_code_scope_py(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".py", ["code"]) is True

    def test_code_scope_md(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".md", ["code"]) is False

    def test_docs_scope_md(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".md", ["docs"]) is True

    def test_docs_scope_py(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".py", ["docs"]) is False

    def test_all_scope(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".css", ["all"]) is True
        assert sa._match_scope(".py", ["all"]) is True

    def test_combined_scope(self):
        sa = SearchAgent(".")
        assert sa._match_scope(".py", ["code", "docs"]) is True
        assert sa._match_scope(".md", ["code", "docs"]) is True
        assert sa._match_scope(".css", ["code", "docs"]) is False


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_finds_matching_files(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("hello world", scope=["code"])
        assert len(results) > 0
        assert any("main.py" in r["path"] for r in results)

    def test_respects_scope_filter(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("project", scope=["docs"])
        assert all(
            r["path"].endswith(".md") or r["path"].endswith(".txt")
            for r in results
        )

    def test_empty_query(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("")
        assert results == []

    def test_no_matches(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("xyznonexistent12345")
        assert results == []

    def test_top_k_limit(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("project", scope=["code", "docs"], top_k=1)
        assert len(results) <= 1

    def test_results_sorted_by_score(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("project", scope=["code", "docs"])
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_snippet_included(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("hello", scope=["code"])
        if results:
            assert results[0]["snippet"] != ""

    def test_nested_files_found(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        results = sa.search("compute", scope=["code"])
        assert len(results) > 0
        assert any("utils.py" in r["path"] for r in results)


# ---------------------------------------------------------------------------
# search_as_tavily
# ---------------------------------------------------------------------------

class TestSearchAsTavily:
    def test_returns_tavily_structure(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        result = sa.search_as_tavily("hello", topic="general", top_k=5)
        assert "results" in result
        assert isinstance(result["results"], list)

    def test_results_have_required_keys(self, sample_repo):
        sa = SearchAgent(str(sample_repo))
        result = sa.search_as_tavily("hello", topic="general", top_k=5)
        for r in result["results"]:
            assert "title" in r
            assert "url" in r
            assert "content" in r
