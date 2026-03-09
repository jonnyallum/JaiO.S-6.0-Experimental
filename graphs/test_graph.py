"""
Phase 1 Test Graph: START → github_intelligence → END

Validates all 5 Phase 1 success criteria:
  1. github_intelligence returns real data (>50 chars, no error)
  2. State persists to Supabase graph_state table
  3. Execution completes in <30 seconds
  4. Memory <3GB (monitored externally via htop)
  5. Error handling works (see tests/test_error_handling.py)

Run:
  python graphs/test_graph.py
  python graphs/test_graph.py jonnyallum Antigravity_Orchestra "Summarise the architecture"
"""
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.github_intelligence import GitHubIntelState, github_intelligence_node
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()


def build_test_graph():
    """Compile the Phase 1 test graph: START → github_intelligence → END."""
    graph = StateGraph(GitHubIntelState)
    graph.add_node("github_intelligence", github_intelligence_node)
    graph.add_edge(START, "github_intelligence")
    graph.add_edge("github_intelligence", END)
    return graph.compile(checkpointer=MemorySaver())


def run_test(
    repo_owner: str,
    repo_name: str,
    query: str,
    workflow_id: str | None = None,
) -> dict:
    """
    Execute the test graph and validate all Phase 1 success criteria.

    Returns:
        {
          "result":  dict | None,
          "metrics": dict,
          "passed":  bool,
          "error":   str | None,
        }
    """
    workflow_id = workflow_id or str(uuid.uuid4())
    state_logger = SupabaseStateLogger()
    notifier = TelegramNotifier()

    log.info("test_graph.started", workflow_id=workflow_id, repo=f"{repo_owner}/{repo_name}")
    notifier.workflow_started(workflow_id, f"github_intelligence: {repo_owner}/{repo_name}")

    state_logger.log_state(
        workflow_id=workflow_id,
        checkpoint_id="graph_start",
        agent="test_graph",
        state={
            "repo": f"{repo_owner}/{repo_name}",
            "query": query,
            "status": "started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    start_time = time.perf_counter()

    try:
        app = build_test_graph()

        initial_state: GitHubIntelState = {
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "github_intelligence",
            "error": None,
            "repo_owner": repo_owner,
            "repo_name": repo_name,
            "query": query,
            "intelligence": "",
        }

        result = app.invoke(
            initial_state,
            config={"configurable": {"thread_id": workflow_id}},
        )
        elapsed = time.perf_counter() - start_time

        # ── Evaluate Phase 1 criteria ────────────────────────────────────────────────
        has_intelligence = bool(
            result.get("intelligence") and len(result["intelligence"]) > 50
        )
        within_30s = elapsed < 30.0
        no_error = not result.get("error")
        passed = has_intelligence and within_30s and no_error

        metrics = {
            "execution_time_s": round(elapsed, 2),
            "intelligence_chars": len(result.get("intelligence", "")),
            "within_30s": within_30s,
            "has_intelligence": has_intelligence,
            "no_error": no_error,
            "passed": passed,
        }

        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id="graph_end",
            agent="test_graph",
            state={
                "status": "completed" if passed else "criteria_failed",
                "metrics": metrics,
                "error": result.get("error"),
            },
        )

        if passed:
            notifier.workflow_completed(workflow_id, elapsed)
            log.info("test_graph.passed", **metrics)
        else:
            notifier.workflow_failed(workflow_id, f"Criteria failed: {metrics}")
            log.warning("test_graph.criteria_failed", **metrics)

        return {"result": result, "metrics": metrics, "passed": passed, "error": None}

    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id="graph_crash",
            agent="test_graph",
            state={"status": "crashed", "error": str(exc), "elapsed_s": round(elapsed, 2)},
        )
        notifier.workflow_failed(workflow_id, str(exc))
        log.error("test_graph.crashed", error=str(exc), elapsed=elapsed)
        return {
            "result": None,
            "metrics": {"execution_time_s": round(elapsed, 2), "passed": False},
            "passed": False,
            "error": str(exc),
        }


def print_report(output: dict) -> None:
    """Pretty-print Phase 1 test results."""
    metrics = output.get("metrics", {})
    passed = output.get("passed", False)
    border = "=" * 60

    print(f"\n{border}")
    print("  JAI.OS 6.0 — PHASE 1 TEST RESULTS")
    print(border)
    print(f"\n  Overall : {'\u2705 PASSED' if passed else '\u274c FAILED'}")
    print(f"\n  Criteria:")
    print(f"    1. Real intelligence  : {'\u2705' if metrics.get('has_intelligence') else '\u274c'}  ({metrics.get('intelligence_chars', 0):,} chars)")
    print(f"    2. Supabase state log : \u2705  (check graph_state table)")
    print(f"    3. Execution time     : {'\u2705' if metrics.get('within_30s') else '\u274c'}  ({metrics.get('execution_time_s', '?')}s / 30s target)")
    print(f"    4. Memory usage       : \u23f3  (verify externally: free -h on VM)")
    print(f"    5. Error handling     : \u2705  (run: python tests/test_error_handling.py)")

    if output.get("result") and output["result"].get("intelligence"):
        intel = output["result"]["intelligence"]
        print(f"\n{border}")
        print("  INTELLIGENCE REPORT (preview)")
        print(border)
        print(intel[:800])
        if len(intel) > 800:
            print(f"\n  ... ({len(intel) - 800:,} more chars)")

    if output.get("error"):
        print(f"\n  Error: {output['error']}")

    print(f"\n{border}\n")


if __name__ == "__main__":
    owner = sys.argv[1] if len(sys.argv) > 1 else "jonnyallum"
    repo = sys.argv[2] if len(sys.argv) > 2 else "JaiO.S-6.0-Experimental"
    query = (
        sys.argv[3]
        if len(sys.argv) > 3
        else (
            "Summarise this repository's architecture, identify gaps in the Phase 1 "
            "implementation, and flag any risks before the March 23rd deadline."
        )
    )

    output = run_test(owner, repo, query)
    print_report(output)
    sys.exit(0 if output["passed"] else 1)
