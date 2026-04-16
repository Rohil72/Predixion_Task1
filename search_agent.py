"""Lightweight search agent: keyword search over repository files.

This intentionally avoids any persistent vector DB; it is a simple, fast
in-process search used during the session. It can also call Tavily for web
results when a TAVILY_API_KEY is present.
"""

import os
import re
import json
from typing import Dict, List, Any
from urllib import request, error


class SearchAgent:
    def __init__(self, root_dir: str = ".") -> None:
        self.root_dir = os.path.abspath(root_dir)

    def _match_scope(self, ext: str, scope: List[str]) -> bool:
        code_ext = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rb", ".sh"}
        doc_ext = {".md", ".txt", ".rst", ".adoc"}
        if "all" in scope:
            return True
        if "code" in scope and ext in code_ext:
            return True
        if "docs" in scope and ext in doc_ext:
            return True
        return False

    def search(self, query: str, scope: List[str] = None, top_k: int = 10) -> List[Dict[str, Any]]:
        """Keyword search over repo files. Returns list of hits with path/score/snippet."""
        if scope is None:
            scope = ["code", "docs"]

        terms = [t.lower() for t in re.findall(r"\w+", query)]
        if not terms:
            return []

        hits: List[Dict[str, Any]] = []

        for root, _, files in os.walk(self.root_dir):
            # skip virtual envs and git dir
            if ".git" in root.lower() or "venv" in root.lower() or "__pycache__" in root:
                continue
            for fn in files:
                path = os.path.join(root, fn)
                try:
                    ext = os.path.splitext(fn)[1].lower()
                except Exception:
                    ext = ""

                if not self._match_scope(ext, scope):
                    continue

                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except Exception:
                    continue

                lc = text.lower()
                score = sum(lc.count(t) for t in terms)
                if score <= 0:
                    continue

                # produce short snippet around first occurrence
                first_idx = min([lc.find(t) for t in terms if lc.find(t) >= 0]) if any(lc.find(t) >= 0 for t in terms) else -1
                snippet = ""
                if first_idx >= 0:
                    start = max(0, first_idx - 120)
                    snippet = text[start : start + 300].replace("\n", " ")

                hits.append({"path": path, "score": score, "snippet": snippet})

        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    def _call_tavily_search(self, query: str, topic: str, top_k: int = 5) -> Dict[str, Any]:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key or api_key == "your_tavily_api_key_here":
            raise RuntimeError("Set TAVILY_API_KEY in env to use web search")

        payload = {
            "query": query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": top_k,
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
            raise RuntimeError(f"Tavily HTTP {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Tavily request failed: {exc}") from exc

    def search_as_tavily(self, query: str, topic: str = "general", top_k: int = 5) -> Dict[str, Any]:
        """Return a Tavily-like response dict by combining repo hits and (optionally) web results."""
        repo_hits = self.search(query, scope=["code", "docs"], top_k=top_k)
        results = []
        for hit in repo_hits:
            title = os.path.basename(hit.get("path", "")) or hit.get("path", "")
            results.append({"title": title, "url": hit.get("path", ""), "content": hit.get("snippet", "")})

        # Try web results if available
        try:
            web_resp = self._call_tavily_search(query, topic, top_k)
            if isinstance(web_resp, dict) and isinstance(web_resp.get("results"), list):
                for r in web_resp.get("results", [])[:top_k]:
                    results.append({"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")})
        except Exception:
            # Ignore web errors and keep local results
            pass

        return {"results": results[:top_k]}


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Simple repo keyword search (plus optional web)")
    p.add_argument("query", nargs="+", help="Query terms")
    p.add_argument("--topk", type=int, default=10)
    args = p.parse_args()
    sa = SearchAgent(".")
    results = sa.search(" ".join(args.query), top_k=args.topk)
    print(json.dumps(results, indent=2))
