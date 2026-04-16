#!/usr/bin/env python3
"""Orchestrator CLI: Planner -> Search -> Researcher (reuse) -> Formatter/Renderer via run_llm.ps1.

This script is intentionally small: it proposes a plan, requires a single 'yes' to run,
collects planner-seeded searches, then calls the existing research_agent with injected
search_fn and seeded_searches. The script prints the raw researcher output to stdout
so the existing formatter/renderer can be invoked (as run_llm.ps1 does).
"""

import argparse
import os
import sys

from terminal_ui import TerminalUI
from planner_agent import Planner
from search_agent import SearchAgent
import research_agent


def summarize_plan_one_line(plan: dict) -> str:
    parts = [f"[{s.get('agent')}] {s.get('title')}" for s in plan.get('steps', [])]
    return " -> ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run orchestrator for a single task")
    parser.add_argument("question_parts", nargs="*", help="Task/question")
    args = parser.parse_args()

    if args.question_parts:
        question = " ".join(args.question_parts).strip()
    else:
        print("Describe the task: ", file=sys.stderr, end="")
        sys.stderr.flush()
        question = input().strip()
    if not question:
        print("No task provided. Exiting.")
        return

    ui = TerminalUI()
    planner = Planner(ui)
    repo_root = os.path.abspath(os.path.dirname(__file__))
    search = SearchAgent(root_dir=repo_root)

    # Propose and allow a single edit loop before approval
    while True:
        plan = planner.propose(question)
        print("\nProposed plan:", file=sys.stderr)
        print("  " + summarize_plan_one_line(plan), file=sys.stderr)
        print("Type 'yes' to run, 'edit' to change the task, or anything else to abort: ", file=sys.stderr, end="")
        sys.stderr.flush()
        resp = input().strip().lower()
        if resp == "edit":
            print("Enter revised task: ", file=sys.stderr, end="")
            sys.stderr.flush()
            question = input().strip()
            continue
        if resp != "yes":
            print("Aborted.", file=sys.stderr)
            return
        break

    # Collect planner-seeded searches and convert to Tavily-like structures
    seeded_searches = []
    for step in plan.get("steps", []):
        if step.get("agent") == "search":
            q = step.get("detail") or question
            tavily_like = search.search_as_tavily(q, topic="general", top_k=10)
            seeded_searches.append({"query": q, "topic": "general", "results": tavily_like.get("results", []), "top_k": 10})

    # Create a search_fn wrapper that matches the research_agent expectation
    def search_fn(q: str, topic: str) -> dict:
        return search.search_as_tavily(q, topic=topic or "general", top_k=10)

    # Run researcher with injected search and seeded evidence
    research_output = research_agent.run_research(question, search_fn=search_fn, seeded_searches=seeded_searches)

    # Print raw output for downstream formatter
    print(research_output)


if __name__ == "__main__":
    main()