import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, List
from urllib import error, request

from guardrails import validate_input, validate_output

RETRIABLE_HTTP_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 2  # seconds


ROOT_DIR = Path(__file__).resolve().parent
PROMPT_PATH = ROOT_DIR / "research_agent_prompt.md"
MAX_SEARCHES = 3
MAX_RESULTS = 5
MAX_MODEL_TURNS = 8
SEARCH_ACTION = "SEARCH"
FINAL_ACTION = "FINAL"
VALID_TOPICS = {"general", "news", "finance"}
REQUIRED_FINAL_HEADINGS = [
    "Executive Summary:",
    "Introduction:",
    "Background:",
    "Analysis:",
    "Key Findings:",
    "Confidence:",
    "Limitations:",
    "Suggested Next Steps:",
    "Sources:",
]
RECENT_TERMS = {
    "latest",
    "recent",
    "today",
    "yesterday",
    "this week",
    "this month",
    "current",
    "currently",
    "new",
    "news",
    "announced",
    "update",
    "updated",
}


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Phase 1 single-agent researcher with Tavily search."
    )
    parser.add_argument(
        "question_parts",
        nargs="*",
        help="Research question. If omitted, stdin is used.",
    )
    return parser.parse_args()


def read_question(question_parts: list[str]) -> str:
    if question_parts:
        question = " ".join(question_parts).strip()
        if question:
            return question

    stdin_text = sys.stdin.read().strip()
    if stdin_text:
        return stdin_text

    raise SystemExit("Pass a research question as arguments or via stdin.")


def load_system_prompt() -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{TODAY}}", datetime.now().strftime("%B %d, %Y"))
        .replace("{{MAX_SEARCHES}}", str(MAX_SEARCHES))
        .replace("{{MAX_RESULTS}}", str(MAX_RESULTS))
    )


def _request_with_retry(req_factory, timeout: int = 90, label: str = "API") -> Any:
    """Execute an HTTP request with exponential-backoff retry on transient errors.

    req_factory must be a callable returning a fresh urllib.request.Request each time
    (because urllib consumes the body on the first attempt).
    """
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
                print(f"[retry] {label} HTTP {exc.code}, retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise SystemExit(f"{label} HTTP {exc.code}: {body}") from exc
        except error.URLError as exc:
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                print(f"[retry] {label} connection error, retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise SystemExit(f"{label} request failed: {exc}") from exc
    raise SystemExit(f"{label} request failed after {MAX_RETRIES} retries") from last_exc


def call_openrouter(messages: list[dict[str, str]]) -> str:
    validate_input(messages)
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_RESEARCH_MODEL") or os.getenv(
        "OPENROUTER_MODEL",
        "openrouter/free",
    )

    if not api_key or api_key == "your_openrouter_api_key_here":
        raise SystemExit("Set OPENROUTER_API_KEY in .env before running the researcher.")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }).encode("utf-8")

    def make_request():
        return request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Research Agent",
            },
            method="POST",
        )

    result = _request_with_retry(make_request, timeout=90, label="OpenRouter")
    result_content = result["choices"][0]["message"]["content"].strip()
    validate_output(result_content)
    return result_content


def call_tavily_search(query: str, topic: str) -> dict[str, Any]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or api_key == "your_tavily_api_key_here":
        raise SystemExit("Set TAVILY_API_KEY in .env before running the researcher.")

    payload = json.dumps({
        "query": query,
        "topic": topic,
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": False,
    }).encode("utf-8")

    def make_request():
        return request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    return _request_with_retry(make_request, timeout=60, label="Tavily")


def detect_default_topic(question: str) -> str:
    lowered = question.lower()
    if any(term in lowered for term in RECENT_TERMS):
        return "news"
    return "general"


def extract_action(response_text: str) -> str:
    match = re.search(r"^\s*ACTION:\s*(SEARCH|FINAL)\s*$", response_text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    return match.group(1).upper()


def extract_field(response_text: str, field_name: str) -> str:
    pattern = rf"^\s*{re.escape(field_name)}:\s*(.+?)\s*$"
    match = re.search(pattern, response_text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def parse_search_request(response_text: str, question: str) -> dict[str, str] | None:
    action = extract_action(response_text)
    if action != SEARCH_ACTION:
        return None

    query = extract_field(response_text, "QUERY")
    topic = extract_field(response_text, "TOPIC").lower() or detect_default_topic(question)

    if topic not in VALID_TOPICS:
        topic = detect_default_topic(question)

    if not query:
        return None

    return {
        "query": query,
        "topic": topic,
    }


def format_search_results(search_count: int, query: str, topic: str, response: dict[str, Any]) -> str:
    results = response.get("results", [])
    if not isinstance(results, list):
        results = []

    lines = [
        f"SEARCH RESULTS {search_count}",
        f"Query: {query}",
        f"Topic: {topic}",
    ]

    if not results:
        lines.append("No results were returned.")
    else:
        for index, result in enumerate(results[:MAX_RESULTS], start=1):
            title = str(result.get("title", "")).strip() or "Untitled result"
            url = str(result.get("url", "")).strip() or "No URL provided"
            content = str(result.get("content", "")).strip() or "No summary available."
            lines.extend(
                [
                    f"{index}. Title: {title}",
                    f"   URL: {url}",
                    f"   Snippet: {content}",
                ]
            )

    lines.append(
        "Use only these results as evidence. You may request one more narrow search or return ACTION: FINAL."
    )
    return "\n".join(lines)


def extract_final_output(response_text: str) -> str:
    action_match = re.search(r"^\s*ACTION:\s*FINAL\s*$", response_text, re.IGNORECASE | re.MULTILINE)
    if not action_match:
        return response_text.strip()

    final_text = response_text[action_match.end() :].strip()
    return final_text or response_text.strip()


def is_final_output_usable(final_text: str) -> bool:
    return all(heading in final_text for heading in REQUIRED_FINAL_HEADINGS)


def run_research(
    question: str,
    search_fn: Optional[Callable[[str, str], dict]] = None,
    seeded_searches: Optional[List[dict]] = None,
    max_model_turns: Optional[int] = None,
) -> str:
    """Run the researcher loop. Optional search_fn should accept (query, topic) and
    return a Tavily-like dict: {"results": [{"title","url","content"}, ...]}.

    seeded_searches: optional list of dicts: {"query":..., "topic":..., "results": [...]}
    """

    messages: list[dict[str, str]] = [
        {"role": "system", "content": load_system_prompt()},
        {"role": "user", "content": f"Research question: {question}"},
    ]

    # Inject any planner-seeded search results so the model can use them as evidence.
    if seeded_searches:
        for idx, seeded in enumerate(seeded_searches, start=1):
            q = seeded.get("query") or question
            topic = seeded.get("topic") or detect_default_topic(question)
            # Normalize seeded results into a Tavily-like response dict
            tavily_resp = {"results": []}
            for r in seeded.get("results", [])[:MAX_RESULTS]:
                title = r.get("title") or r.get("path") or "Untitled"
                url = r.get("url") or r.get("path") or ""
                content = r.get("content") or r.get("snippet") or ""
                tavily_resp["results"].append({"title": title, "url": url, "content": content})
            messages.append({"role": "user", "content": format_search_results(idx, q, topic, tavily_resp)})

    search_count = 0
    invalid_action_count = 0
    final_correction_count = 0
    seen_queries: set[str] = set()
    turns = max_model_turns or MAX_MODEL_TURNS

    def perform_search(q: str, topic: str) -> dict[str, Any]:
        # Prefer injected search function (from planner or orchestrator); fall back to Tavily.
        if search_fn:
            try:
                return search_fn(q, topic)
            except Exception:
                # Fall back to Tavily if injected search fails
                try:
                    return call_tavily_search(q, topic)
                except Exception:
                    return {"results": []}
        return call_tavily_search(q, topic)

    for _ in range(turns):
        response_text = call_openrouter(messages)
        messages.append({"role": "assistant", "content": response_text})

        action = extract_action(response_text)

        if action == SEARCH_ACTION:
            if search_count >= MAX_SEARCHES:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Search budget is exhausted. Return ACTION: FINAL now using the evidence already gathered."
                        ),
                    }
                )
                continue

            search_request = parse_search_request(response_text, question)
            if not search_request:
                invalid_action_count += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your SEARCH action was invalid. Return exactly:\n"
                            "ACTION: SEARCH\nQUERY: <query>\nTOPIC: <general|news|finance>\nRATIONALE: <short sentence>"
                        ),
                    }
                )
                if invalid_action_count >= 2:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Stop searching and return ACTION: FINAL using the best available evidence.",
                        }
                    )
                continue

            normalized_query = search_request["query"].strip().lower()
            if normalized_query in seen_queries:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That search query repeats a previous search too closely. Narrow it materially or return ACTION: FINAL."
                        ),
                    }
                )
                continue

            seen_queries.add(normalized_query)
            search_count += 1
            tavily_response = perform_search(search_request["query"], search_request["topic"])
            messages.append(
                {
                    "role": "user",
                    "content": format_search_results(
                        search_count,
                        search_request["query"],
                        search_request["topic"],
                        tavily_response,
                    ),
                }
            )
            continue

        if action == FINAL_ACTION:
            final_text = extract_final_output(response_text)
            if is_final_output_usable(final_text):
                return final_text

            if final_correction_count == 0:
                final_correction_count += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your FINAL response must follow the full research brief template and include these headings exactly:\n"
                            + "\n".join(f"- {heading}" for heading in REQUIRED_FINAL_HEADINGS)
                            + "\nReturn ACTION: FINAL again in the required template."
                        ),
                    }
                )
                continue

            return final_text

        invalid_action_count += 1
        messages.append(
            {
                "role": "user",
                "content": (
                    "Return exactly one of the two valid modes:\n"
                    "1. ACTION: SEARCH\n"
                    "2. ACTION: FINAL\n"
                    "Do not answer in any other format."
                ),
            }
        )

        if invalid_action_count >= 2:
            messages.append(
                {
                    "role": "user",
                    "content": "Return ACTION: FINAL now using the best available evidence.",
                }
            )

    messages.append(
        {
            "role": "user",
            "content": (
                "The interaction budget is exhausted. Return ACTION: FINAL now using the best available evidence."
            ),
        }
    )
    forced_final_response = call_openrouter(messages)
    return extract_final_output(forced_final_response)


def main() -> None:
    load_env_file()
    # Ensure stdout can handle Unicode (avoids UnicodeEncodeError on Windows consoles)
    try:
        if getattr(sys.stdout, "encoding", None) != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # If reconfigure is not available or fails, continue and let print handle encoding
        pass
    args = parse_args()
    question = read_question(args.question_parts)
    print(run_research(question))


if __name__ == "__main__":
    main()
