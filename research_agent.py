import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


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


def call_openrouter(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_RESEARCH_MODEL") or os.getenv(
        "OPENROUTER_MODEL",
        "openrouter/free",
    )

    if not api_key or api_key == "your_openrouter_api_key_here":
        raise SystemExit("Set OPENROUTER_API_KEY in .env before running the researcher.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }

    req = request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Research Agent",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc

    return result["choices"][0]["message"]["content"].strip()


def call_tavily_search(query: str, topic: str) -> dict[str, Any]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or api_key == "your_tavily_api_key_here":
        raise SystemExit("Set TAVILY_API_KEY in .env before running the researcher.")

    payload = {
        "query": query,
        "topic": topic,
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": False,
    }

    req = request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Tavily HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Tavily request failed: {exc}") from exc


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


def run_research(question: str) -> str:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": load_system_prompt()},
        {"role": "user", "content": f"Research question: {question}"},
    ]

    search_count = 0
    invalid_action_count = 0
    final_correction_count = 0
    seen_queries: set[str] = set()

    for _ in range(MAX_MODEL_TURNS):
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
            tavily_response = call_tavily_search(search_request["query"], search_request["topic"])
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
