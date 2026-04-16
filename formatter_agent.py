import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

RETRIABLE_HTTP_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 2


ROOT_DIR = Path(__file__).resolve().parent
REPORT_SCHEMA_PATH = ROOT_DIR / "research_report.schema.json"
RESULT_SCHEMA_PATH = ROOT_DIR / "formatter_result.schema.json"
STANDARD_SECTION_ORDER = ["Introduction", "Background", "Analysis"]

SYSTEM_PROMPT = """You are a formatter agent in a research pipeline.

Your job is to take raw upstream model output and convert it into one strict JSON object with exactly two top-level keys:
- report
- diagnostics

The upstream input may be plain prose, markdown, bullets, or JSON.

The report contract is document-native, not summary-native.
Preserve rich narrative structure instead of collapsing everything into a short answer.

Rules:
1. Use only the upstream text and the optional user question hint.
2. Do not search, browse, plan, or invent facts.
3. Rewrite for clarity, compression, and presentation quality when useful.
4. Populate report.executive_summary as a concise executive summary.
5. Populate report.sections as the main narrative body in standard corporate order:
   - Introduction
   - Background
   - Analysis
   - any additional custom sections if the upstream output supports them
6. Each section should contain paragraph strings in `content` and optional structured `subsections`.
7. Extract and normalize citations only if they are explicitly present in the upstream text.
8. A usable citation must contain enough information to populate a `sources_used` entry, including a URL or path-like locator.
9. If no usable citations are present, return a fallback result:
   - diagnostics.status = "fallback"
   - report.sources_used = []
   - report.confidence_level = "low"
   - diagnostics.fallback_reason must explain that citations were missing or unusable
10. If citation coverage is limited, return status "ok" but add explicit warnings and limitations.
11. If the user question hint is provided, preserve it exactly in report.user_question.
12. If the user question hint is not provided, infer the question from upstream output if possible. If it is unclear, use a concise placeholder and add a warning.
13. If confidence is not explicitly stated upstream, make a conservative estimate from the evidence quality and add a warning.
14. Keep sources in report.sources_used rather than embedding them in narrative sections.
15. Never output markdown, prose outside JSON, or code fences.
16. report must follow the provided schema exactly.
17. diagnostics must follow the provided schema exactly.
"""


def load_env_file(path: str = ".env") -> None:
    env_path = ROOT_DIR / path
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Format raw upstream model output into the research report contract."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="Optional path to a file containing raw upstream model output or a small JSON wrapper.",
    )
    parser.add_argument(
        "--question",
        dest="user_question_hint",
        default="",
        help="Optional user question hint to force into report.user_question.",
    )
    return parser.parse_args()


def read_input_text(input_path: str | None) -> str:
    if input_path:
        return Path(input_path).read_text(encoding="utf-8")

    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("Pass a file path or pipe raw upstream model output to stdin.")

    return raw


def coerce_formatter_input(raw_text: str, user_question_hint: str) -> dict[str, str]:
    stripped = raw_text.strip()
    if not stripped:
        raise SystemExit("Formatter input is empty.")

    question_hint = user_question_hint.strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {
            "user_question_hint": question_hint,
            "raw_model_output": stripped,
        }

    if isinstance(parsed, dict):
        raw_model_output = parsed.get("raw_model_output")
        inferred_question = ""
        for key in ("user_question_hint", "user_question", "question"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                inferred_question = value.strip()
                break

        if isinstance(raw_model_output, str) and raw_model_output.strip():
            return {
                "user_question_hint": question_hint or inferred_question,
                "raw_model_output": raw_model_output.strip(),
            }

        return {
            "user_question_hint": question_hint or inferred_question,
            "raw_model_output": json.dumps(parsed, indent=2),
        }

    if isinstance(parsed, str):
        return {
            "user_question_hint": question_hint,
            "raw_model_output": parsed.strip(),
        }

    return {
        "user_question_hint": question_hint,
        "raw_model_output": json.dumps(parsed, indent=2),
    }


def normalize_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return ""

    parsed = parse.urlsplit(stripped)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def normalize_sources(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for source in sources:
        title = str(source.get("title", "")).strip()
        url = normalize_url(str(source.get("url", "")))
        source_type = str(source.get("source_type", "")).strip()
        used_for = str(source.get("used_for", "")).strip()

        if not title or not url or not source_type or not used_for:
            continue

        key = (url, title.lower())
        if key in seen:
            continue

        seen.add(key)
        normalized.append(
            {
                "title": title,
                "url": url,
                "source_type": source_type,
                "used_for": used_for,
            }
        )

    return normalized


def append_unique(items: list[str], value: str) -> None:
    cleaned = value.strip()
    if cleaned and cleaned not in items:
        items.append(cleaned)


def validate_string_list(name: str, value: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{name} must be an array of strings.")


def validate_subsection(subsection: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(subsection, dict):
        return [f"{path} must be an object."]

    required_keys = {"title", "content"}
    extra_keys = set(subsection.keys()) - required_keys
    missing_keys = required_keys - set(subsection.keys())

    if missing_keys:
        errors.append(f"{path} is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"{path} has unexpected keys: {sorted(extra_keys)}")

    if "title" in subsection and not isinstance(subsection["title"], str):
        errors.append(f"{path}.title must be a string.")

    validate_string_list(f"{path}.content", subsection.get("content"), errors)
    return errors


def validate_section(section: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(section, dict):
        return [f"{path} must be an object."]

    required_keys = {"title", "content", "subsections"}
    extra_keys = set(section.keys()) - required_keys
    missing_keys = required_keys - set(section.keys())

    if missing_keys:
        errors.append(f"{path} is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"{path} has unexpected keys: {sorted(extra_keys)}")

    if "title" in section and not isinstance(section["title"], str):
        errors.append(f"{path}.title must be a string.")

    validate_string_list(f"{path}.content", section.get("content"), errors)

    subsections = section.get("subsections")
    if not isinstance(subsections, list):
        errors.append(f"{path}.subsections must be an array.")
    else:
        for index, subsection in enumerate(subsections):
            errors.extend(validate_subsection(subsection, f"{path}.subsections[{index}]"))

    return errors


def validate_report(report: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be an object."]

    required_keys = {
        "user_question",
        "executive_summary",
        "sections",
        "key_findings",
        "sources_used",
        "confidence_level",
        "limitations_or_assumptions",
        "suggested_next_steps",
    }
    extra_keys = set(report.keys()) - required_keys
    missing_keys = required_keys - set(report.keys())

    if missing_keys:
        errors.append(f"report is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"report has unexpected keys: {sorted(extra_keys)}")

    for key in ("user_question", "executive_summary", "confidence_level"):
        if key in report and not isinstance(report[key], str):
            errors.append(f"report.{key} must be a string.")

    if report.get("confidence_level") not in {"low", "medium", "high"}:
        errors.append("report.confidence_level must be one of: low, medium, high.")

    sections = report.get("sections")
    if not isinstance(sections, list):
        errors.append("report.sections must be an array.")
    else:
        for index, section in enumerate(sections):
            errors.extend(validate_section(section, f"report.sections[{index}]"))

    validate_string_list("report.key_findings", report.get("key_findings"), errors)
    validate_string_list(
        "report.limitations_or_assumptions",
        report.get("limitations_or_assumptions"),
        errors,
    )
    validate_string_list(
        "report.suggested_next_steps",
        report.get("suggested_next_steps"),
        errors,
    )

    sources = report.get("sources_used")
    if not isinstance(sources, list):
        errors.append("report.sources_used must be an array.")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"report.sources_used[{index}] must be an object.")
                continue

            required_source_keys = {"title", "url", "source_type", "used_for"}
            extra_source_keys = set(source.keys()) - required_source_keys
            missing_source_keys = required_source_keys - set(source.keys())

            if missing_source_keys:
                errors.append(
                    f"report.sources_used[{index}] is missing keys: {sorted(missing_source_keys)}"
                )
            if extra_source_keys:
                errors.append(
                    f"report.sources_used[{index}] has unexpected keys: {sorted(extra_source_keys)}"
                )

            for source_key in required_source_keys:
                if source_key in source and not isinstance(source[source_key], str):
                    errors.append(
                        f"report.sources_used[{index}].{source_key} must be a string."
                    )

            if source.get("source_type") not in {"web", "file", "database", "api"}:
                errors.append(
                    f"report.sources_used[{index}].source_type must be one of: web, file, database, api."
                )

    return errors


def validate_diagnostics(diagnostics: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(diagnostics, dict):
        return ["diagnostics must be an object."]

    required_keys = {"status", "warnings", "validation_errors", "fallback_reason"}
    extra_keys = set(diagnostics.keys()) - required_keys
    missing_keys = required_keys - set(diagnostics.keys())

    if missing_keys:
        errors.append(f"diagnostics is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"diagnostics has unexpected keys: {sorted(extra_keys)}")

    if diagnostics.get("status") not in {"ok", "fallback"}:
        errors.append("diagnostics.status must be either ok or fallback.")

    validate_string_list("diagnostics.warnings", diagnostics.get("warnings"), errors)
    validate_string_list(
        "diagnostics.validation_errors",
        diagnostics.get("validation_errors"),
        errors,
    )

    if not isinstance(diagnostics.get("fallback_reason"), str):
        errors.append("diagnostics.fallback_reason must be a string.")

    return errors


def validate_formatter_result(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return ["Formatter result must be an object."]

    errors: list[str] = []
    required_keys = {"report", "diagnostics"}
    extra_keys = set(result.keys()) - required_keys
    missing_keys = required_keys - set(result.keys())

    if missing_keys:
        errors.append(f"Formatter result is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"Formatter result has unexpected keys: {sorted(extra_keys)}")

    if "report" in result:
        errors.extend(validate_report(result["report"]))
    if "diagnostics" in result:
        errors.extend(validate_diagnostics(result["diagnostics"]))

    return errors


def validate_formatter_input(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["Formatter input must be an object."]

    errors: list[str] = []
    required_keys = {"raw_model_output"}
    allowed_keys = {"user_question_hint", "raw_model_output"}

    extra_keys = set(payload.keys()) - allowed_keys
    missing_keys = required_keys - set(payload.keys())

    if missing_keys:
        errors.append(f"Formatter input is missing keys: {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"Formatter input has unexpected keys: {sorted(extra_keys)}")

    if "user_question_hint" in payload and not isinstance(payload["user_question_hint"], str):
        errors.append("user_question_hint must be a string.")

    if not isinstance(payload.get("raw_model_output"), str) or not payload["raw_model_output"].strip():
        errors.append("raw_model_output must be a non-empty string.")

    return errors


def build_fallback_result(
    payload: dict[str, str],
    reason: str,
    validation_errors: list[str] | None = None,
    warnings: list[str] | None = None,
    user_question: str | None = None,
) -> dict[str, Any]:
    question = (user_question or payload.get("user_question_hint", "")).strip()
    if not question:
        question = "Not explicitly provided in upstream output."

    fallback_warnings = list(warnings or [])
    if not payload.get("user_question_hint", "").strip():
        append_unique(
            fallback_warnings,
            "User question was not supplied as a hint; fallback question may be inferred or generic.",
        )

    return {
        "report": {
            "user_question": question,
            "executive_summary": "Fallback: the upstream model output did not provide usable citations, so a citation-backed research document could not be produced safely.",
            "sections": [
                {
                    "title": "Background",
                    "content": [
                        "The formatter could not preserve a full research narrative because the upstream model output did not provide usable citations."
                    ],
                    "subsections": [],
                }
            ],
            "key_findings": [],
            "sources_used": [],
            "confidence_level": "low",
            "limitations_or_assumptions": [
                "No usable citations were available in the upstream model output.",
                "Per formatter rules, the response falls back instead of inventing sources.",
            ],
            "suggested_next_steps": [
                "Re-run the upstream model with explicit citation capture in the response.",
                "Ensure the upstream output includes source titles and usable URLs before formatting.",
            ],
        },
        "diagnostics": {
            "status": "fallback",
            "warnings": fallback_warnings,
            "validation_errors": validation_errors or [],
            "fallback_reason": reason,
        },
    }


def _request_with_retry(req_factory, timeout: int = 90, label: str = "API") -> Any:
    """Execute an HTTP request with exponential-backoff retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        req = req_factory()
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRIABLE_HTTP_CODES and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                print(f"[retry] {label} HTTP {exc.code}, retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise SystemExit(f"{label} HTTP {exc.code}: {body}") from exc
        except error.URLError as exc:
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                print(f"[retry] {label} connection error, retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise SystemExit(f"{label} request failed: {exc}") from exc
    raise SystemExit(f"{label} request failed after {MAX_RETRIES} retries") from last_exc


def call_openrouter(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_FORMATTER_MODEL") or os.getenv(
        "OPENROUTER_MODEL",
        "openrouter/free",
    )

    if not api_key or api_key == "your_openrouter_api_key_here":
        raise SystemExit("Set OPENROUTER_API_KEY in .env before running the formatter.")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0,
    }).encode("utf-8")

    def make_request():
        return request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Research Formatter Agent",
            },
            method="POST",
        )

    result = _request_with_retry(make_request, timeout=90, label="Formatter")
    return result["choices"][0]["message"]["content"].strip()


def parse_json_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def build_user_prompt(payload: dict[str, str]) -> str:
    report_schema = load_json_file(REPORT_SCHEMA_PATH)
    result_schema = load_json_file(RESULT_SCHEMA_PATH)

    hint_text = payload["user_question_hint"] or "(not provided)"

    return (
        "Format the upstream model output into the required result schema.\n\n"
        "Result schema:\n"
        f"{json.dumps(result_schema, indent=2)}\n\n"
        "Report schema:\n"
        f"{json.dumps(report_schema, indent=2)}\n\n"
        "User question hint:\n"
        f"{hint_text}\n\n"
        "Raw upstream model output:\n"
        f"{payload['raw_model_output']}"
    )


def section_priority(title: str) -> tuple[int, str]:
    normalized = title.strip().lower()
    order_map = {name.lower(): index for index, name in enumerate(STANDARD_SECTION_ORDER)}
    return (order_map.get(normalized, len(order_map)), normalized)


def normalize_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_sections: list[dict[str, Any]] = []
    for section in sections:
        title = str(section.get("title", "")).strip()
        if not title:
            continue

        content = [
            str(item).strip()
            for item in section.get("content", [])
            if isinstance(item, str) and item.strip()
        ]
        subsections = []
        for subsection in section.get("subsections", []):
            if not isinstance(subsection, dict):
                continue
            subtitle = str(subsection.get("title", "")).strip()
            if not subtitle:
                continue
            subcontent = [
                str(item).strip()
                for item in subsection.get("content", [])
                if isinstance(item, str) and item.strip()
            ]
            subsections.append(
                {
                    "title": subtitle,
                    "content": subcontent,
                }
            )

        normalized_sections.append(
            {
                "title": title,
                "content": content,
                "subsections": subsections,
            }
        )

    return sorted(normalized_sections, key=lambda section: section_priority(section["title"])[0])


def apply_pipeline_rules(payload: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    report = result["report"]
    diagnostics = result["diagnostics"]

    report["sources_used"] = normalize_sources(report["sources_used"])
    report["sections"] = normalize_sections(report["sections"])

    if payload["user_question_hint"].strip():
        report["user_question"] = payload["user_question_hint"].strip()
    elif not report["user_question"].strip():
        report["user_question"] = "Not explicitly provided in upstream output."
        append_unique(
            diagnostics["warnings"],
            "User question could not be clearly inferred from the upstream output.",
        )

    if not report["sources_used"]:
        return build_fallback_result(
            payload,
            "The upstream model output did not contain usable citations after formatting.",
            validation_errors=diagnostics["validation_errors"],
            warnings=diagnostics["warnings"],
            user_question=report["user_question"],
        )

    if len(report["sources_used"]) == 1:
        append_unique(
            diagnostics["warnings"],
            "Only one usable citation was extracted from the upstream output.",
        )
        append_unique(
            report["limitations_or_assumptions"],
            "Only one usable citation was extracted from the upstream output.",
        )

    if not report["sections"]:
        append_unique(
            diagnostics["warnings"],
            "Narrative sections were sparse; the document may be thinner than the source material intended.",
        )

    diagnostics["status"] = "ok"
    diagnostics["fallback_reason"] = ""
    return result


def format_payload(payload: dict[str, str]) -> dict[str, Any]:
    input_errors = validate_formatter_input(payload)
    if input_errors:
        raise SystemExit("Invalid formatter input:\n- " + "\n- ".join(input_errors))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(payload)},
    ]

    validation_errors: list[str] = []
    for attempt in range(2):
        raw_response = call_openrouter(messages)
        try:
            result = parse_json_response(raw_response)
        except json.JSONDecodeError as exc:
            validation_errors = [f"Model returned invalid JSON: {exc}"]
        else:
            validation_errors = validate_formatter_result(result)
            if not validation_errors:
                result = apply_pipeline_rules(payload, result)
                revalidation_errors = validate_formatter_result(result)
                if not revalidation_errors:
                    return result
                validation_errors = revalidation_errors

        if attempt == 0:
            messages.append({"role": "assistant", "content": raw_response})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your last response failed validation.\n"
                        "Return only a corrected JSON object.\n"
                        "Validation errors:\n- " + "\n- ".join(validation_errors)
                    ),
                }
            )

    return build_fallback_result(
        payload,
        "Formatter output failed validation after repair attempts.",
        validation_errors=validation_errors,
    )


def main() -> None:
    load_env_file()
    args = parse_args()
    raw_text = read_input_text(args.input_path)
    payload = coerce_formatter_input(raw_text, args.user_question_hint)
    result = format_payload(payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
