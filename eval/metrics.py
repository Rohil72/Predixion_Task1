"""Evaluation metrics for the research agent pipeline.

Scoring functions for:
- Structural completeness (required report headings)
- Citation quality (valid URLs, source coverage)
- Answer relevance (keyword overlap and optional LLM-as-judge)
- Pipeline health (fallback rate, validation error rate)
"""

import re
from typing import Any, Dict, List
from urllib.parse import urlparse


REQUIRED_REPORT_KEYS = {
    "user_question",
    "executive_summary",
    "sections",
    "key_findings",
    "confidence_level",
    "limitations_or_assumptions",
    "suggested_next_steps",
    "sources_used",
}

STANDARD_SECTION_TITLES = {"Introduction", "Background", "Analysis"}


def structural_completeness(report: Dict[str, Any]) -> Dict[str, Any]:
    """Score how complete the report structure is (0.0 to 1.0)."""
    if not isinstance(report, dict):
        return {"score": 0.0, "missing_keys": list(REQUIRED_REPORT_KEYS), "details": "Not a dict"}

    present = set(report.keys()) & REQUIRED_REPORT_KEYS
    missing = REQUIRED_REPORT_KEYS - present
    key_score = len(present) / len(REQUIRED_REPORT_KEYS) if REQUIRED_REPORT_KEYS else 1.0

    # Check section titles
    sections = report.get("sections", [])
    section_titles = {s.get("title", "").strip() for s in sections if isinstance(s, dict)}
    section_coverage = len(section_titles & STANDARD_SECTION_TITLES) / len(STANDARD_SECTION_TITLES) if STANDARD_SECTION_TITLES else 1.0

    # Check non-empty content
    has_summary = bool(str(report.get("executive_summary", "")).strip())
    has_findings = bool(report.get("key_findings"))
    content_score = (int(has_summary) + int(has_findings)) / 2

    overall = (key_score * 0.4) + (section_coverage * 0.3) + (content_score * 0.3)

    return {
        "score": round(overall, 3),
        "key_score": round(key_score, 3),
        "section_coverage": round(section_coverage, 3),
        "content_score": round(content_score, 3),
        "missing_keys": sorted(missing),
        "section_titles_found": sorted(section_titles),
    }


def citation_quality(report: Dict[str, Any]) -> Dict[str, Any]:
    """Score the quality and validity of citations (0.0 to 1.0)."""
    sources = report.get("sources_used", [])
    if not isinstance(sources, list):
        return {"score": 0.0, "total_sources": 0, "valid_urls": 0, "details": "sources_used not a list"}

    total = len(sources)
    if total == 0:
        return {"score": 0.0, "total_sources": 0, "valid_urls": 0, "details": "No sources"}

    valid_urls = 0
    has_title = 0
    has_used_for = 0

    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url", "")).strip()
        if url:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                valid_urls += 1
        if str(source.get("title", "")).strip():
            has_title += 1
        if str(source.get("used_for", "")).strip():
            has_used_for += 1

    url_ratio = valid_urls / total
    title_ratio = has_title / total
    used_for_ratio = has_used_for / total

    # Penalize having only 1 source
    quantity_factor = min(total / 3, 1.0)

    overall = (url_ratio * 0.4 + title_ratio * 0.2 + used_for_ratio * 0.2 + quantity_factor * 0.2)

    return {
        "score": round(overall, 3),
        "total_sources": total,
        "valid_urls": valid_urls,
        "has_title": has_title,
        "has_used_for": has_used_for,
    }


def answer_relevance(report: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Score how relevant the report content is to the question (keyword overlap heuristic)."""
    if not question.strip():
        return {"score": 0.0, "details": "No question provided"}

    # Extract keywords from question
    question_terms = set(re.findall(r"\w{3,}", question.lower()))
    if not question_terms:
        return {"score": 0.0, "details": "No meaningful terms in question"}

    # Gather all text from report
    text_parts: List[str] = []
    text_parts.append(str(report.get("executive_summary", "")))
    text_parts.append(str(report.get("user_question", "")))
    for finding in report.get("key_findings", []):
        text_parts.append(str(finding))
    for section in report.get("sections", []):
        if isinstance(section, dict):
            for content_item in section.get("content", []):
                text_parts.append(str(content_item))
            for sub in section.get("subsections", []):
                if isinstance(sub, dict):
                    for content_item in sub.get("content", []):
                        text_parts.append(str(content_item))

    all_text = " ".join(text_parts).lower()
    report_terms = set(re.findall(r"\w{3,}", all_text))

    # Keyword overlap
    overlap = question_terms & report_terms
    coverage = len(overlap) / len(question_terms) if question_terms else 0.0

    # Content volume penalty (very short reports score lower)
    word_count = len(all_text.split())
    volume_factor = min(word_count / 100, 1.0)

    overall = coverage * 0.7 + volume_factor * 0.3

    return {
        "score": round(overall, 3),
        "question_terms": sorted(question_terms),
        "matched_terms": sorted(overlap),
        "coverage": round(coverage, 3),
        "word_count": word_count,
    }


def pipeline_health(result: Dict[str, Any]) -> Dict[str, Any]:
    """Assess pipeline health from diagnostics."""
    diagnostics = result.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        return {"score": 0.0, "status": "unknown", "details": "No diagnostics"}

    status = str(diagnostics.get("status", "")).strip()
    warnings = diagnostics.get("warnings", [])
    validation_errors = diagnostics.get("validation_errors", [])

    is_ok = status == "ok"
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    error_count = len(validation_errors) if isinstance(validation_errors, list) else 0

    # Score: ok=1.0, fallback=0.3, then penalize warnings/errors
    base = 1.0 if is_ok else 0.3
    penalty = min(warning_count * 0.05 + error_count * 0.15, 0.5)
    overall = max(base - penalty, 0.0)

    return {
        "score": round(overall, 3),
        "status": status,
        "warning_count": warning_count,
        "validation_error_count": error_count,
    }


def score_result(result: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    """Compute all metrics for a single pipeline result."""
    report = result.get("report", {})

    structure = structural_completeness(report)
    citations = citation_quality(report)
    relevance = answer_relevance(report, question)
    health = pipeline_health(result)

    composite = (
        structure["score"] * 0.25
        + citations["score"] * 0.25
        + relevance["score"] * 0.25
        + health["score"] * 0.25
    )

    return {
        "composite_score": round(composite, 3),
        "structural_completeness": structure,
        "citation_quality": citations,
        "answer_relevance": relevance,
        "pipeline_health": health,
    }
