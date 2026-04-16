"""Minimal planning/orchestrator agent with session-only memory."""

from typing import Any, Callable, Dict, List
import time
import sys


class Planner:
    def __init__(self, ui=None):
        self.ui = ui
        self.session_memory: Dict[str, Any] = {"interactions": []}

    def propose(self, task: str) -> Dict[str, Any]:
        steps = [
            {
                "id": "clarify",
                "title": "Clarify task",
                "agent": "user",
                "detail": f"Clarify objectives for: {task}",
                "required": True,
            },
            {
                "id": "search",
                "title": "Search for relevant resources",
                "agent": "search",
                "detail": task,
                "required": False,
            },
            {
                "id": "synthesize",
                "title": "Synthesize findings",
                "agent": "planner",
                "detail": task,
                "required": False,
            },
        ]
        plan = {"task": task, "steps": steps}
        self.session_memory["plan"] = plan
        return plan

    def run(self, plan: Dict[str, Any], search_fn: Callable[..., List[Dict[str, Any]]]):
        for step in plan.get("steps", []):
            agent = step.get("agent", "planner")
            title = step.get("title", "")
            if self.ui:
                self.ui.show_status(agent, title)

            if agent == "user":
                print(f"{title}\n{step.get('detail')}\nEnter clarification (or blank to continue): ", file=sys.stderr, end="")
                sys.stderr.flush()
                resp = input().strip()
                self.session_memory["interactions"].append({"step": step["id"], "input": resp})

            elif agent == "search":
                query = step.get("detail") or plan.get("task")
                results = search_fn(query, scope=["code", "docs"], top_k=10)
                self.session_memory.setdefault("search_results", []).append({"query": query, "results": results})
                print(f"\nTop {len(results)} search hits:", file=sys.stderr)
                for i, r in enumerate(results, 1):
                    print(f"{i}. {r.get('path')} (score={r.get('score',0)})\n{r.get('snippet')}\n", file=sys.stderr)
                print("Press Enter to continue...", file=sys.stderr, end="")
                sys.stderr.flush()
                input()

            else:
                snippets: List[str] = []
                for batch in self.session_memory.get("search_results", []):
                    for hit in batch.get("results", [])[:3]:
                        snippets.append(hit.get("snippet", ""))
                draft = "\n---\n".join(snippets[:5]) or "(no content found)"
                self.session_memory["draft"] = draft
                print("\nDraft synthesis:\n", file=sys.stderr)
                print(draft, file=sys.stderr)
                print("Press Enter to continue...", file=sys.stderr, end="")
                sys.stderr.flush()
                input()

            time.sleep(0.05)

        if self.ui:
            self.ui.show_status("planner", "Plan complete")


if __name__ == "__main__":
    from terminal_ui import TerminalUI
    from search_agent import SearchAgent

    ui = TerminalUI()
    planner = Planner(ui)
    task = input("Describe the task: ")
    plan = planner.propose(task)
    print("Proposed plan:")
    for s in plan["steps"]:
        print(f"- [{s['agent']}] {s['title']}")

    if input("Run? (y/n): ").strip().lower().startswith("y"):
        sa = SearchAgent(".")
        planner.run(plan, sa.search)
