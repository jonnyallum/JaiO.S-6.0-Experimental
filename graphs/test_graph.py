"""test_graph.py — Phase 1 proof-of-concept.

Graph topology: START → @hugo → END

Validates all 5 Phase 1 success criteria:
  1. @hugo returns real GitHub intelligence (not stub data)
  2. State persists to Supabase graph_state table
  3. Execution completes in <30 seconds
  4. Memory usage <3GB on GCP e2-medium (manual check via htop)
  5. Error handling — graceful failure on invalid repo

Usage:
    python graphs/test_graph.py
    python graphs/test_graph.py --repo owner/name --query "your question"
"""

import time
import uuid
import argparse
import sys

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from agents.hugo import hugo_node, HugoState
from tools.supabase_state import persist_state


def build_test_graph():
    """Build START → @hugo → END with in-memory checkpointing."""
    checkpointer = MemorySaver()

    graph = StateGraph(HugoState)
    graph.add_node("hugo", hugo_node)
    graph.add_edge(START, "hugo")
    graph.add_edge("hugo", END)

    return graph.compile(checkpointer=checkpointer)


def run_test(
    repo_owner: str = "jonnyallum",
    repo_name: str = "Antigravity_Orchestra",
    query: str = "What are the most critical components of this system? Summarise the architecture.",
) -> tuple[dict, float, bool]:
    """Execute the Phase 1 test and print results.

    Returns (result, elapsed_seconds, all_passed).
    """
    workflow_id = str(uuid.uuid4())

    print(f"\n{'='*64}")
    print("  Jai.OS 6.0 — Phase 1 Test Graph")
    print(f"{'='*64}")
    print(f"  Repo:        {repo_owner}/{repo_name}")
    print(f"  Query:       {query}")
    print(f"  Workflow ID: {workflow_id}")
    print(f"{'='*64}\n")

    app = build_test_graph()

    initial_state: HugoState = {
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "query": query,
        "intelligence": "",
        "error": None,
    }

    config = {"configurable": {"thread_id": workflow_id}}

    # --- Execute ----------------------------------------------------------
    start = time.time()
    try:
        result = app.invoke(initial_state, config=config)
    except Exception as e:
        print(f"\n❌ Graph execution failed: {e}")
        return {}, time.time() - start, False

    elapsed = time.time() - start

    # --- Print intelligence -----------------------------------------------
    print("\n=== INTELLIGENCE ===")
    print(result.get("intelligence", "(empty)"))

    # --- Persist to Supabase ---------------------------------------------
    print("\n=== SUPABASE STATE PERSISTENCE ===")
    persisted = persist_state(workflow_id, result)

    # --- Evaluate criteria ------------------------------------------------
    intel = result.get("intelligence", "")
    checks = {
        "@hugo returns real intelligence (not stub)": bool(intel) and "STUB" not in intel,
        "State persisted to Supabase":               persisted,
        "Execution time <30s":                       elapsed < 30,
        "No errors":                                 result.get("error") is None,
    }

    print(f"\n=== PHASE 1 CHECKLIST (execution: {elapsed:.1f}s) ===")
    all_passed = True
    for label, passed in checks.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon}  {label}")
        if not passed:
            all_passed = False

    verdict = "🟢 ALL CHECKS PASS — GO for Phase 2" if all_passed else "🔴 SOME CHECKS FAILED — review above"
    print(f"\n{verdict}\n")

    return result, elapsed, all_passed


def run_error_handling_test():
    """Test graceful failure with an invalid repo."""
    print("\n=== ERROR HANDLING TEST ===")
    app = build_test_graph()
    bad_state: HugoState = {
        "repo_owner": "this-does-not-exist-xyzabc999",
        "repo_name":  "fake-repo-xyzabc",
        "query":      "test",
        "intelligence": "",
        "error": None,
    }
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    try:
        app.invoke(bad_state, config=config)
        print("❌ Expected an exception but none was raised")
    except Exception as e:
        print(f"✅ Graceful failure on invalid repo: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 1 test graph")
    parser.add_argument("--repo", default="jonnyallum/Antigravity_Orchestra", help="owner/repo")
    parser.add_argument("--query", default="What are the most critical components of this system? Summarise the architecture.")
    parser.add_argument("--error-test", action="store_true", help="Run error handling test only")
    args = parser.parse_args()

    if args.error_test:
        run_error_handling_test()
        sys.exit(0)

    owner, _, name = args.repo.partition("/")
    result, elapsed, passed = run_test(repo_owner=owner, repo_name=name, query=args.query)

    # Also run error handling test at the end
    run_error_handling_test()

    sys.exit(0 if passed else 1)
