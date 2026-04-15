import argparse
import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "renderer" / "output"
HISTORY_DIR = OUTPUT_DIR / "history"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a formatted research report JSON object into HTML."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="Optional path to a file containing formatter output JSON.",
    )
    return parser.parse_args()


def read_input_text(input_path: str | None) -> str:
    if input_path:
        return Path(input_path).read_text(encoding="utf-8")

    stdin_text = sys.stdin.read()
    if not stdin_text.strip():
        raise SystemExit("Pass a formatter output file path or pipe JSON into stdin.")

    return stdin_text


def parse_formatter_output(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Renderer received invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SystemExit("Renderer input must be a JSON object.")

    if "report" not in payload or "diagnostics" not in payload:
        raise SystemExit("Renderer input must contain top-level 'report' and 'diagnostics' keys.")

    return payload


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def paragraphize(text: str) -> str:
    cleaned = escape(text.strip())
    if not cleaned:
        return '<p class="muted">Not provided.</p>'
    return f"<p>{cleaned}</p>"


def render_paragraphs(items: list[str], css_class: str = "") -> str:
    cleaned_items = [escape(item.strip()) for item in items if isinstance(item, str) and item.strip()]
    if not cleaned_items:
        return '<p class="muted">Not provided.</p>'

    class_attr = f' class="{css_class}"' if css_class else ""
    return "".join(f"<p{class_attr}>{item}</p>" for item in cleaned_items)


def render_string_list(items: list[str], css_class: str = "") -> str:
    cleaned_items = [escape(item.strip()) for item in items if isinstance(item, str) and item.strip()]
    if not cleaned_items:
        return '<p class="muted">Not provided.</p>'

    class_attr = f' class="{css_class}"' if css_class else ""
    rows = "".join(f"<li>{item}</li>" for item in cleaned_items)
    return f"<ul{class_attr}>{rows}</ul>"


def render_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return '<p class="muted">No usable citations were available.</p>'

    parts: list[str] = ['<ol class="sources-list">']
    for source in sources:
        title = escape(str(source.get("title", "")).strip() or "Untitled source")
        url = escape(str(source.get("url", "")).strip())
        source_type = escape(str(source.get("source_type", "")).strip())
        used_for = escape(str(source.get("used_for", "")).strip())

        link_html = f'<a href="{url}" target="_blank" rel="noreferrer">{url}</a>' if url else ""
        meta = " | ".join(part for part in [source_type, used_for] if part)
        meta_html = f'<div class="source-meta">{meta}</div>' if meta else ""

        parts.append(
            "<li>"
            f'<div class="source-title">{title}</div>'
            f"{meta_html}"
            f'<div class="source-link">{link_html}</div>'
            "</li>"
        )
    parts.append("</ol>")
    return "".join(parts)


def render_notice(diagnostics: dict[str, Any]) -> str:
    status = str(diagnostics.get("status", "")).strip()
    warnings = ensure_list(diagnostics.get("warnings"))
    fallback_reason = str(diagnostics.get("fallback_reason", "")).strip()

    if status == "fallback":
        reason_html = f"<p>{escape(fallback_reason)}</p>" if fallback_reason else ""
        warnings_html = render_string_list([str(item) for item in warnings], "notice-list")
        return (
            '<section class="notice notice-fallback">'
            "<h2>Formatter Fallback</h2>"
            f"{reason_html}"
            f"{warnings_html}"
            "</section>"
        )

    if warnings:
        warnings_html = render_string_list([str(item) for item in warnings], "notice-list")
        return (
            '<section class="notice notice-warning">'
            "<h2>Review Notes</h2>"
            f"{warnings_html}"
            "</section>"
        )

    return ""


def render_sections(sections: list[dict[str, Any]]) -> str:
    if not sections:
        return ""

    parts: list[str] = []
    for section in sections:
        title = escape(str(section.get("title", "")).strip() or "Untitled Section")
        content = ensure_list(section.get("content"))
        subsections = ensure_list(section.get("subsections"))

        subsection_html_parts: list[str] = []
        for subsection in subsections:
            if not isinstance(subsection, dict):
                continue
            subtitle = escape(str(subsection.get("title", "")).strip() or "Untitled Subsection")
            subcontent = render_paragraphs(ensure_list(subsection.get("content")))
            subsection_html_parts.append(
                '<div class="subsection">'
                f"<h3>{subtitle}</h3>"
                f"{subcontent}"
                "</div>"
            )

        parts.append(
            '<section class="section narrative-section">'
            f"<h2>{title}</h2>"
            f"{render_paragraphs(content)}"
            f"{''.join(subsection_html_parts)}"
            "</section>"
        )

    return "".join(parts)


def build_html(payload: dict[str, Any]) -> str:
    report = payload["report"]
    diagnostics = payload["diagnostics"]

    question = str(report.get("user_question", "")).strip() or "Untitled research question"
    executive_summary = str(report.get("executive_summary", "")).strip()
    sections = ensure_list(report.get("sections"))
    findings = [str(item) for item in ensure_list(report.get("key_findings"))]
    confidence = str(report.get("confidence_level", "")).strip()
    limitations = [str(item) for item in ensure_list(report.get("limitations_or_assumptions"))]
    next_steps = [str(item) for item in ensure_list(report.get("suggested_next_steps"))]
    sources = ensure_list(report.get("sources_used"))
    validation_errors = [str(item) for item in ensure_list(diagnostics.get("validation_errors"))]

    generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    notice_html = render_notice(diagnostics)
    sections_html = render_sections(sections)
    diagnostics_html = ""
    if validation_errors:
        diagnostics_html = (
            '<section class="section appendix">'
            "<h2>Validation Notes</h2>"
            f"{render_string_list(validation_errors)}"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(question)}</title>
  <style>
    :root {{
      --page-bg: #e9edf2;
      --paper: #ffffff;
      --ink: #1f2933;
      --muted: #5f6c7b;
      --rule: #d6dde5;
      --accent: #445a72;
      --accent-soft: #eef3f8;
      --fallback: #f8ece6;
      --warning: #f4f1e7;
      --shadow: 0 18px 50px rgba(26, 39, 52, 0.12);
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: linear-gradient(180deg, #f3f6f9 0%, var(--page-bg) 100%);
      color: var(--ink);
      font-family: Georgia, Cambria, "Times New Roman", Times, serif;
      line-height: 1.72;
    }}

    .shell {{
      min-height: 100vh;
      padding: 32px 18px 48px;
    }}

    .page {{
      width: min(920px, 100%);
      margin: 0 auto;
      background: var(--paper);
      box-shadow: var(--shadow);
      border: 1px solid rgba(68, 90, 114, 0.08);
    }}

    .page-inner {{
      padding: 56px 64px 64px;
    }}

    .eyebrow,
    .meta,
    .confidence-chip,
    .source-meta,
    .source-link {{
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }}

    .header {{
      border-bottom: 1px solid var(--rule);
      padding-bottom: 24px;
      margin-bottom: 28px;
    }}

    .eyebrow {{
      margin: 0 0 10px;
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(2rem, 3vw, 2.7rem);
      line-height: 1.15;
      font-weight: 700;
      color: #16202b;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 18px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .notice {{
      margin: 0 0 28px;
      padding: 18px 20px;
      border-left: 4px solid var(--accent);
      background: var(--accent-soft);
    }}

    .notice-fallback {{
      background: var(--fallback);
      border-left-color: #9a5d3d;
    }}

    .notice-warning {{
      background: var(--warning);
      border-left-color: #8a7442;
    }}

    .notice h2,
    .section h2 {{
      margin: 0 0 12px;
      font-size: 1.15rem;
      line-height: 1.3;
      color: #182431;
    }}

    .section {{
      padding-top: 24px;
      margin-top: 24px;
      border-top: 1px solid var(--rule);
    }}

    .section:first-of-type {{
      border-top: none;
      margin-top: 0;
      padding-top: 0;
    }}

    .narrative-section p + p,
    .summary p + p,
    .subsection p + p {{
      margin-top: 14px;
    }}

    p {{
      margin: 0;
    }}

    ul,
    ol {{
      margin: 0;
      padding-left: 24px;
    }}

    li + li {{
      margin-top: 10px;
    }}

    .muted {{
      color: var(--muted);
    }}

    .summary p {{
      font-size: 1.06rem;
    }}

    .subsection {{
      margin-top: 18px;
      padding-top: 12px;
    }}

    .subsection h3 {{
      margin: 0 0 10px;
      font-size: 1rem;
      color: #243444;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}

    .sources-list {{
      padding-left: 22px;
    }}

    .source-title {{
      font-weight: 700;
    }}

    .source-meta,
    .source-link {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    a {{
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(68, 90, 114, 0.22);
    }}

    a:hover {{
      border-bottom-color: rgba(68, 90, 114, 0.55);
    }}

    .confidence-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .confidence-chip {{
      display: inline-flex;
      align-items: center;
      padding: 6px 12px;
      border: 1px solid var(--rule);
      background: #f8fafc;
      text-transform: capitalize;
      color: #243444;
      font-size: 0.92rem;
      font-weight: 600;
    }}

    .appendix {{
      color: var(--muted);
    }}

    @media (max-width: 720px) {{
      .shell {{
        padding: 0;
      }}

      .page {{
        width: 100%;
        box-shadow: none;
        border: none;
      }}

      .page-inner {{
        padding: 28px 20px 32px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <main class="page">
      <div class="page-inner">
        <header class="header">
          <p class="eyebrow">Research Brief</p>
          <h1>{escape(question)}</h1>
          <div class="meta">
            <span>Generated: {escape(generated_at)}</span>
            <span>Formatter status: {escape(str(diagnostics.get("status", "unknown")).capitalize())}</span>
          </div>
        </header>

        {notice_html}

        <section class="section">
          <h2>Executive Summary</h2>
          <div class="summary">{paragraphize(executive_summary)}</div>
        </section>

        {sections_html}

        <section class="section">
          <h2>Key Findings</h2>
          {render_string_list(findings)}
        </section>

        <section class="section">
          <h2>Confidence Level</h2>
          <div class="confidence-row">
            <span class="confidence-chip">{escape(confidence or "Not provided")}</span>
          </div>
        </section>

        <section class="section">
          <h2>Limitations and Assumptions</h2>
          {render_string_list(limitations)}
        </section>

        <section class="section">
          <h2>Suggested Next Steps</h2>
          {render_string_list(next_steps)}
        </section>

        <section class="section">
          <h2>Sources</h2>
          {render_sources(sources)}
        </section>

        {diagnostics_html}
      </div>
    </main>
  </div>
</body>
</html>
"""


def write_outputs(payload: dict[str, Any], html_text: str) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    latest_json_path = OUTPUT_DIR / "latest_report.json"
    latest_html_path = OUTPUT_DIR / "latest_report.html"
    history_json_path = HISTORY_DIR / f"report_{timestamp}.json"
    history_html_path = HISTORY_DIR / f"report_{timestamp}.html"

    json_text = json.dumps(payload, indent=2)

    latest_json_path.write_text(json_text, encoding="utf-8")
    latest_html_path.write_text(html_text, encoding="utf-8")
    history_json_path.write_text(json_text, encoding="utf-8")
    history_html_path.write_text(html_text, encoding="utf-8")

    return {
        "latest_json_path": str(latest_json_path),
        "latest_html_path": str(latest_html_path),
        "history_json_path": str(history_json_path),
        "history_html_path": str(history_html_path),
    }


def main() -> None:
    args = parse_args()
    raw_text = read_input_text(args.input_path)
    payload = parse_formatter_output(raw_text)
    html_text = build_html(payload)
    output_paths = write_outputs(payload, html_text)
    print(json.dumps(output_paths, indent=2))


if __name__ == "__main__":
    main()
