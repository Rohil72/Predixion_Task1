"""Unit tests for formatter_agent.py — validation, coercion, and fallback logic."""

import json
import sys
from pathlib import Path

# Ensure project root is on path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pytest

from formatter_agent import (
    coerce_formatter_input,
    validate_formatter_input,
    validate_formatter_result,
    validate_report,
    validate_diagnostics,
    normalize_sources,
    normalize_url,
    normalize_sections,
    build_fallback_result,
    apply_pipeline_rules,
)


# ---------------------------------------------------------------------------
# coerce_formatter_input
# ---------------------------------------------------------------------------

class TestCoerceFormatterInput:
    def test_plain_text_input(self):
        result = coerce_formatter_input("This is raw text output", "")
        assert result["raw_model_output"] == "This is raw text output"
        assert result["user_question_hint"] == ""

    def test_json_with_raw_model_output(self):
        data = json.dumps({"raw_model_output": "Some model output", "user_question_hint": "What is X?"})
        result = coerce_formatter_input(data, "")
        assert result["raw_model_output"] == "Some model output"
        assert result["user_question_hint"] == "What is X?"

    def test_cli_question_override(self):
        data = json.dumps({"raw_model_output": "text", "user_question_hint": "old question"})
        result = coerce_formatter_input(data, "new question from CLI")
        assert result["user_question_hint"] == "new question from CLI"

    def test_empty_input_raises(self):
        with pytest.raises(SystemExit):
            coerce_formatter_input("", "")

    def test_json_string_input(self):
        result = coerce_formatter_input(json.dumps("just a string"), "")
        assert result["raw_model_output"] == "just a string"


# ---------------------------------------------------------------------------
# validate_formatter_input
# ---------------------------------------------------------------------------

class TestValidateFormatterInput:
    def test_valid_input(self):
        errors = validate_formatter_input({"raw_model_output": "text"})
        assert errors == []

    def test_missing_raw_model_output(self):
        errors = validate_formatter_input({})
        assert any("raw_model_output" in e for e in errors)

    def test_empty_raw_model_output(self):
        errors = validate_formatter_input({"raw_model_output": "   "})
        assert any("non-empty" in e for e in errors)

    def test_extra_keys(self):
        errors = validate_formatter_input({"raw_model_output": "text", "extra": True})
        assert any("unexpected" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_report
# ---------------------------------------------------------------------------

class TestValidateReport:
    @staticmethod
    def _minimal_report():
        return {
            "user_question": "Test?",
            "executive_summary": "Summary",
            "sections": [],
            "key_findings": ["finding"],
            "sources_used": [],
            "confidence_level": "medium",
            "limitations_or_assumptions": ["limit"],
            "suggested_next_steps": ["step"],
        }

    def test_valid_report(self):
        errors = validate_report(self._minimal_report())
        assert errors == []

    def test_invalid_confidence(self):
        report = self._minimal_report()
        report["confidence_level"] = "very_high"
        errors = validate_report(report)
        assert any("confidence_level" in e for e in errors)

    def test_missing_keys(self):
        errors = validate_report({"user_question": "Test?"})
        assert len(errors) > 0

    def test_invalid_source_type(self):
        report = self._minimal_report()
        report["sources_used"] = [
            {"title": "t", "url": "http://x.com", "source_type": "magic", "used_for": "stuff"}
        ]
        errors = validate_report(report)
        assert any("source_type" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_diagnostics
# ---------------------------------------------------------------------------

class TestValidateDiagnostics:
    def test_valid_diagnostics(self):
        d = {"status": "ok", "warnings": [], "validation_errors": [], "fallback_reason": ""}
        assert validate_diagnostics(d) == []

    def test_invalid_status(self):
        d = {"status": "bad", "warnings": [], "validation_errors": [], "fallback_reason": ""}
        errors = validate_diagnostics(d)
        assert any("status" in e for e in errors)


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

class TestNormalizeUrl:
    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_keeps_root_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_empty_string(self):
        assert normalize_url("") == ""


# ---------------------------------------------------------------------------
# normalize_sources
# ---------------------------------------------------------------------------

class TestNormalizeSources:
    def test_deduplicates(self):
        sources = [
            {"title": "Test", "url": "https://example.com", "source_type": "web", "used_for": "ref"},
            {"title": "Test", "url": "https://example.com", "source_type": "web", "used_for": "ref"},
        ]
        result = normalize_sources(sources)
        assert len(result) == 1

    def test_drops_incomplete(self):
        sources = [
            {"title": "", "url": "https://example.com", "source_type": "web", "used_for": "ref"},
        ]
        result = normalize_sources(sources)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# normalize_sections
# ---------------------------------------------------------------------------

class TestNormalizeSections:
    def test_orders_by_standard_priority(self):
        sections = [
            {"title": "Analysis", "content": ["text"], "subsections": []},
            {"title": "Introduction", "content": ["text"], "subsections": []},
            {"title": "Background", "content": ["text"], "subsections": []},
        ]
        result = normalize_sections(sections)
        titles = [s["title"] for s in result]
        assert titles == ["Introduction", "Background", "Analysis"]

    def test_strips_empty_titles(self):
        sections = [
            {"title": "", "content": ["text"], "subsections": []},
            {"title": "Analysis", "content": ["text"], "subsections": []},
        ]
        result = normalize_sections(sections)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# build_fallback_result
# ---------------------------------------------------------------------------

class TestBuildFallbackResult:
    def test_produces_valid_result(self):
        payload = {"user_question_hint": "Q?", "raw_model_output": "text"}
        result = build_fallback_result(payload, "test reason")
        errors = validate_formatter_result(result)
        assert errors == []
        assert result["diagnostics"]["status"] == "fallback"
        assert result["report"]["confidence_level"] == "low"


# ---------------------------------------------------------------------------
# validate_formatter_result (end-to-end)
# ---------------------------------------------------------------------------

class TestValidateFormatterResult:
    def test_valid_full_result(self):
        result = {
            "report": {
                "user_question": "Q?",
                "executive_summary": "Summary",
                "sections": [],
                "key_findings": [],
                "sources_used": [],
                "confidence_level": "low",
                "limitations_or_assumptions": [],
                "suggested_next_steps": [],
            },
            "diagnostics": {
                "status": "ok",
                "warnings": [],
                "validation_errors": [],
                "fallback_reason": "",
            },
        }
        assert validate_formatter_result(result) == []

    def test_rejects_non_dict(self):
        errors = validate_formatter_result("not a dict")
        assert len(errors) > 0
