"""LLM-powered planning agent with session memory and deterministic fallback.

The planner calls the LLM to decompose a user question into a task-specific
plan, producing a list of steps with agent assignments.  If the LLM call
fails or returns invalid JSON, it falls back to a safe deterministic plan.
"""

import json
import os
import sys
import time
from typing import Any, Dict, List
from urllib import error, request

RETRIABLE_HTTP_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 2

PLANNER_SYSTEM_PROMPT = """\
You are a planning agent in a research pipeline.
Your job is to decompose a user question into an ordered list of steps.

Available agents:
- "search"   : Performs web or repository searches to gather evidence.
- "research" : Synthesizes evidence into a structured research report.

Rules:
1. Return ONLY a JSON object. No markdown, no prose, no code fences.
2. The JSON must have exactly one key: "steps" (an array).
3. Each step object has these keys:
   - "id"       : short unique slug (e.g. "search_companies", "research_report")
   - "title"    : human-readable one-line summary
   - "agent"    : one of "search" or "research"
   - "detail"   : a search query (for search steps) or instruction (for research)
   - "required" : boolean, true if this step must succeed
4. Start with 1-3 search steps to gather evidence, end with exactly one research step.
5. Tailor searches to the question type:
   - Factual: search for specific facts
   - Comparative: search each entity separately
   - Current events: use time-specific queries
   - Domain-specific: use domain terminology
6. Keep queries concrete and narrow.

Example output:
{
  "steps": [
    {"id": "search_main", "title": "Search for core topic", "agent": "search", "detail": "quantum computing breakthroughs 2025", "required": true},
    {"id": "search_compare", "title": "Search competitor landscape", "agent": "search", "detail": "IBM vs Google quantum supremacy comparison", "required": false},
    {"id": "synthesize", "title": "Produce research report", "agent": "research", "detail": "Synthesize findings into a structured report", "required": true}
  ]
}
"""


def _load_env_file(path: str = ".env") -> None:
    """Load .env variables if not already set."""
    from pathlib import Path

    env_path = Path(__file__).resolve().parent / path
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _call_openrouter(messages: list[dict[str, str]]) -> str:
    """Call OpenRouter with retry logic for the planner model."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_PLANNER_MODEL") or os.getenv(
        "OPENROUTER_MODEL", "openrouter/free"
    )
    if not api_key or api_key == "your_openrouter_api_key_here":
        raise RuntimeError("Set OPENROUTER_API_KEY in .env")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }).encode("utf-8")

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        req = request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Research Planner Agent",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
        except error.HTTPError as exc:
            if exc.code in RETRIABLE_HTTP_CODES and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                print(f"[retry] Planner HTTP {exc.code}, retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise RuntimeError(f"Planner HTTP {exc.code}") from exc
        except error.URLError as exc:
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                print(f"[retry] Planner connection error, retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
                last_exc = exc
                continue
            raise RuntimeError(f"Planner request failed: {exc}") from exc
    raise RuntimeError("Planner request failed after retries") from last_exc


def _parse_plan_json(text: str) -> Dict[str, Any]:
    """Extract and parse a JSON plan from the LLM response."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _build_fallback_plan(task: str) -> Dict[str, Any]:
    """Deterministic fallback plan when the LLM is unavailable or returns garbage."""
    return {
        "task": task,
        "steps": [
            {
                "id": "search_main",
                "title": "Search for relevant information",
                "agent": "search",
                "detail": task,
                "required": True,
            },
            {
                "id": "synthesize",
                "title": "Synthesize findings into report",
                "agent": "research",
                "detail": task,
                "required": True,
            },
        ],
    }


def _validate_plan(plan: Dict[str, Any], task: str) -> Dict[str, Any]:
    """Validate and normalize a plan from the LLM."""
    if not isinstance(plan, dict) or "steps" not in plan:
        raise ValueError("Plan must contain a 'steps' key")

    steps = plan["steps"]
    if not isinstance(steps, list) or len(steps) == 0:
        raise ValueError("Plan must have at least one step")

    valid_agents = {"search", "research"}
    validated_steps: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent", "")).strip()
        if agent not in valid_agents:
            continue
        validated_steps.append({
            "id": str(step.get("id", f"step_{len(validated_steps)}")).strip(),
            "title": str(step.get("title", "")).strip() or "Untitled step",
            "agent": agent,
            "detail": str(step.get("detail", task)).strip() or task,
            "required": bool(step.get("required", False)),
        })

    if not validated_steps:
        raise ValueError("No valid steps found after filtering")

    # Ensure there's at least one search step and ends with research
    has_search = any(s["agent"] == "search" for s in validated_steps)
    has_research = any(s["agent"] == "research" for s in validated_steps)

    if not has_search:
        validated_steps.insert(0, {
            "id": "search_auto",
            "title": "Search for information",
            "agent": "search",
            "detail": task,
            "required": True,
        })
    if not has_research:
        validated_steps.append({
            "id": "synthesize_auto",
            "title": "Synthesize research report",
            "agent": "research",
            "detail": task,
            "required": True,
        })

    return {"task": task, "steps": validated_steps}


class Planner:
    def __init__(self, ui=None):
        self.ui = ui
        self.session_memory: Dict[str, Any] = {"interactions": []}

    def propose(self, task: str) -> Dict[str, Any]:
        """Generate a task-specific plan using the LLM, with deterministic fallback."""
        try:
            messages = [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Plan how to research this question:\n{task}"},
            ]
            raw = _call_openrouter(messages)
            plan_data = _parse_plan_json(raw)
            plan = _validate_plan(plan_data, task)
            print(f"[planner] LLM-generated plan with {len(plan['steps'])} steps", file=sys.stderr)
        except Exception as exc:
            print(f"[planner] LLM planning failed ({exc}), using fallback plan", file=sys.stderr)
            plan = _build_fallback_plan(task)

        self.session_memory["plan"] = plan
        return plan


if __name__ == "__main__":
    from terminal_ui import TerminalUI

    _load_env_file()
    ui = TerminalUI()
    planner = Planner(ui)
    task = input("Describe the task: ")
    plan = planner.propose(task)
    print("Proposed plan:")
    for s in plan["steps"]:
        print(f"- [{s['agent']}] {s['title']}: {s.get('detail', '')}")
