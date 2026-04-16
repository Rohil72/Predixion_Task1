"""Evaluation harness: runs test questions through the pipeline and scores results.

Usage:
    python -m eval.eval_harness                     # run all test questions
    python -m eval.eval_harness --ids factual_basic  # run specific questions
    python -m eval.eval_harness --dry-run            # validate setup without API calls
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Ensure the project root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from eval.metrics import score_result


def load_test_questions(path: Path | None = None) -> List[Dict[str, Any]]:
    """Load test questions from JSON file."""
    if path is None:
        path = Path(__file__).resolve().parent / "test_questions.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_pipeline(question: str, timeout_seconds: int = 300) -> Dict[str, Any]:
    """Run the full research + formatter pipeline for a single question.

    Returns the formatter JSON output (report + diagnostics).
    """
    from research_agent import load_env_file, run_research
    from formatter_agent import coerce_formatter_input, format_payload

    load_env_file()

    start = time.time()
    raw_output = run_research(question)
    research_time = time.time() - start

    payload = coerce_formatter_input(raw_output, question)

    start = time.time()
    result = format_payload(payload)
    formatter_time = time.time() - start

    result["_meta"] = {
        "research_time_s": round(research_time, 2),
        "formatter_time_s": round(formatter_time, 2),
        "total_time_s": round(research_time + formatter_time, 2),
    }

    return result


def evaluate_single(
    test_case: Dict[str, Any], dry_run: bool = False
) -> Dict[str, Any]:
    """Run and evaluate a single test case."""
    question = test_case["question"]
    test_id = test_case["id"]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[eval] Running: {test_id}", file=sys.stderr)
    print(f"[eval] Question: {question}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if dry_run:
        return {
            "test_id": test_id,
            "question": question,
            "status": "dry_run",
            "scores": None,
        }

    try:
        result = run_pipeline(question)
        scores = score_result(result, question)

        # Check expected keywords
        expected = test_case.get("expected_keywords", [])
        if expected:
            report_text = json.dumps(result.get("report", {})).lower()
            matched = [kw for kw in expected if kw.lower() in report_text]
            keyword_coverage = len(matched) / len(expected) if expected else 1.0
            scores["keyword_coverage"] = {
                "score": round(keyword_coverage, 3),
                "expected": expected,
                "matched": matched,
            }

        return {
            "test_id": test_id,
            "question": question,
            "type": test_case.get("type", "unknown"),
            "difficulty": test_case.get("difficulty", "unknown"),
            "status": "success",
            "scores": scores,
            "meta": result.get("_meta", {}),
        }

    except Exception as exc:
        return {
            "test_id": test_id,
            "question": question,
            "status": "error",
            "error": str(exc),
            "scores": None,
        }


def print_summary(results: List[Dict[str, Any]]) -> None:
    """Print an evaluation summary table."""
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"{'ID':<25} {'Status':<10} {'Composite':<10} {'Structure':<10} {'Citations':<10} {'Relevance':<10} {'Health':<10}")
    print("-" * 95)

    scores_list: List[float] = []
    for r in results:
        test_id = r["test_id"][:24]
        status = r["status"]
        if r["scores"]:
            s = r["scores"]
            composite = s["composite_score"]
            scores_list.append(composite)
            print(
                f"{test_id:<25} {status:<10} {composite:<10.3f} "
                f"{s['structural_completeness']['score']:<10.3f} "
                f"{s['citation_quality']['score']:<10.3f} "
                f"{s['answer_relevance']['score']:<10.3f} "
                f"{s['pipeline_health']['score']:<10.3f}"
            )
        else:
            print(f"{test_id:<25} {status:<10} {'N/A':<10}")

    if scores_list:
        avg = sum(scores_list) / len(scores_list)
        print("-" * 95)
        print(f"{'AVERAGE':<25} {'':10} {avg:<10.3f}")
        print(f"\nTotal: {len(results)} | Passed: {sum(1 for r in results if r['status'] == 'success')} | Failed: {sum(1 for r in results if r['status'] == 'error')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation harness")
    parser.add_argument("--ids", nargs="*", help="Specific test IDs to run")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup without API calls")
    parser.add_argument("--output", type=str, help="Path to save results JSON")
    args = parser.parse_args()

    questions = load_test_questions()

    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
        if not questions:
            print(f"No matching test IDs found. Available: {[q['id'] for q in load_test_questions()]}", file=sys.stderr)
            return

    results: List[Dict[str, Any]] = []
    for test_case in questions:
        result = evaluate_single(test_case, dry_run=args.dry_run)
        results.append(result)

    print_summary(results)

    # Save full results
    output_path = args.output or str(ROOT_DIR / "eval" / f"eval_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nFull results saved to: {output_path}")


if __name__ == "__main__":
    main()
