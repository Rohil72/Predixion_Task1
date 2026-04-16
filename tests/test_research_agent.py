"""Unit tests for research_agent.py — action parsing, search extraction, output validation."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pytest

from research_agent import (
    extract_action,
    extract_field,
    parse_search_request,
    format_search_results,
    extract_final_output,
    is_final_output_usable,
    detect_default_topic,
    SEARCH_ACTION,
    FINAL_ACTION,
    REQUIRED_FINAL_HEADINGS,
)


# ---------------------------------------------------------------------------
# extract_action
# ---------------------------------------------------------------------------

class TestExtractAction:
    def test_search_action(self):
        assert extract_action("ACTION: SEARCH\nQUERY: test") == "SEARCH"

    def test_final_action(self):
        assert extract_action("ACTION: FINAL\nSome output") == "FINAL"

    def test_case_insensitive(self):
        assert extract_action("action: search\nQUERY: test") == "SEARCH"

    def test_no_action(self):
        assert extract_action("This is just a response") == ""

    def test_action_with_leading_whitespace(self):
        assert extract_action("  ACTION: FINAL\nOutput") == "FINAL"

    def test_action_mid_text(self):
        text = "Some preamble\nACTION: SEARCH\nQUERY: test\nMore text"
        assert extract_action(text) == "SEARCH"


# ---------------------------------------------------------------------------
# extract_field
# ---------------------------------------------------------------------------

class TestExtractField:
    def test_query_field(self):
        text = "ACTION: SEARCH\nQUERY: quantum computing advances\nTOPIC: general"
        assert extract_field(text, "QUERY") == "quantum computing advances"

    def test_topic_field(self):
        text = "ACTION: SEARCH\nQUERY: test\nTOPIC: news"
        assert extract_field(text, "TOPIC") == "news"

    def test_missing_field(self):
        assert extract_field("ACTION: SEARCH\nQUERY: test", "TOPIC") == ""

    def test_strips_whitespace(self):
        text = "QUERY:   spaced query   "
        assert extract_field(text, "QUERY") == "spaced query"


# ---------------------------------------------------------------------------
# parse_search_request
# ---------------------------------------------------------------------------

class TestParseSearchRequest:
    def test_valid_search_request(self):
        text = "ACTION: SEARCH\nQUERY: quantum computing\nTOPIC: general\nRATIONALE: test"
        result = parse_search_request(text, "original question")
        assert result is not None
        assert result["query"] == "quantum computing"
        assert result["topic"] == "general"

    def test_missing_query(self):
        text = "ACTION: SEARCH\nTOPIC: general"
        result = parse_search_request(text, "q")
        assert result is None

    def test_invalid_topic_defaults(self):
        text = "ACTION: SEARCH\nQUERY: test\nTOPIC: invalid_topic"
        result = parse_search_request(text, "regular question")
        assert result is not None
        assert result["topic"] == "general"

    def test_news_topic_from_question(self):
        text = "ACTION: SEARCH\nQUERY: test"
        result = parse_search_request(text, "What are the latest developments?")
        assert result is not None
        assert result["topic"] == "news"

    def test_non_search_action(self):
        text = "ACTION: FINAL\nSome output"
        result = parse_search_request(text, "q")
        assert result is None


# ---------------------------------------------------------------------------
# detect_default_topic
# ---------------------------------------------------------------------------

class TestDetectDefaultTopic:
    def test_news_keywords(self):
        assert detect_default_topic("What are the latest AI developments?") == "news"
        assert detect_default_topic("recent changes in policy") == "news"
        assert detect_default_topic("What was announced today?") == "news"

    def test_general_default(self):
        assert detect_default_topic("Explain quantum computing") == "general"
        assert detect_default_topic("Compare REST vs GraphQL") == "general"


# ---------------------------------------------------------------------------
# format_search_results
# ---------------------------------------------------------------------------

class TestFormatSearchResults:
    def test_formats_results(self):
        response = {
            "results": [
                {"title": "Result 1", "url": "https://example.com/1", "content": "Snippet 1"},
                {"title": "Result 2", "url": "https://example.com/2", "content": "Snippet 2"},
            ]
        }
        text = format_search_results(1, "test query", "general", response)
        assert "SEARCH RESULTS 1" in text
        assert "test query" in text
        assert "Result 1" in text
        assert "https://example.com/1" in text

    def test_no_results(self):
        text = format_search_results(1, "test", "general", {"results": []})
        assert "No results" in text

    def test_missing_fields(self):
        response = {"results": [{"title": "", "url": "", "content": ""}]}
        text = format_search_results(1, "test", "general", response)
        assert "Untitled result" in text


# ---------------------------------------------------------------------------
# extract_final_output
# ---------------------------------------------------------------------------

class TestExtractFinalOutput:
    def test_extracts_after_action_final(self):
        text = "ACTION: FINAL\nThis is the final report content"
        result = extract_final_output(text)
        assert result == "This is the final report content"

    def test_no_action_returns_full_text(self):
        text = "Just some text without action markers"
        result = extract_final_output(text)
        assert result == text.strip()

    def test_empty_after_action(self):
        text = "ACTION: FINAL"
        result = extract_final_output(text)
        assert result == text.strip()


# ---------------------------------------------------------------------------
# is_final_output_usable
# ---------------------------------------------------------------------------

class TestIsFinalOutputUsable:
    def test_all_headings_present(self):
        text = "\n".join(REQUIRED_FINAL_HEADINGS) + "\nMore content"
        assert is_final_output_usable(text) is True

    def test_missing_heading(self):
        headings = REQUIRED_FINAL_HEADINGS[:-1]  # Drop last heading
        text = "\n".join(headings)
        assert is_final_output_usable(text) is False

    def test_empty_text(self):
        assert is_final_output_usable("") is False
