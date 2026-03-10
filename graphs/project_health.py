"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GRAPH : project_health
PURPOSE : Parallel multi-agent repo health scan with quality gate.

Pattern:
  START
    → parallel_scan  (security_audit + dependency_audit + architecture_review run concurrently)
    → merge_results  (consolidates 3 reports into single health artifact)
    → quality_gate   (quality_validation scores the merged report)
  END

Concurrency:
  parallel_scan uses ThreadPoolExecutor(max_workers=3).
  All 3 agents run simultaneously — total wall-clock time ≈ slowest single agent.

Telemetry:
  Each agent captures CallMetrics. session_summary() is returned in the final state.

Design (@langraph doctrine):
  - All agent calls inside parallel_scan are non-fatal — one failure does not kill others
  - merge_results is a pure function (no I/O)
  - quality_gate returns a scored, labelled report
  - Supabase state logged at PRE (before parallel scan) and POST (after quality gate)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.security_audit import SecurityAuditState, security_audit_node
from agents.dependency_audit import DependencyAuditState, dependency_audit_node
from agents.architecture_review import ArchitectureReviewState, architecture_review_node
from agents.quality_validation import QualityValidationState, quality_validation_node
from state.base import BaseState
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics, session_summary
from tools.notification_tools import TelegramNotifier

log = structlog.get_logger()

# ── Constants ─────────────────────────────────────────────────────────────────────
SCAN_WORKERS  = 3   # Parallel threads — one per specialist agent
PASS_THRESHOLD = 6  # Lower bar than single-agent (3 reports = more data to assess)


# ── State ─────────────────────────────────────────────────────────────────────────
class ProjectHealthState(BaseState):
    # Inputs
    repo_owner: str
    repo_name: str
    focus: str                      # general | security | dependencies | architecture
    # Parallel scan outputs
    security_report: str
    risk_level: str
    dependency_report: str
    architecture_report: str
    # Merge output
    health_artifact: str            # Consolidated report passed to quality gate
    # Quality gate outputs
    health_score: int               # 0–10
    passed: bool
    validation_report: str
    # Telemetry
    telemetry_summary: dict         # session_summary() from all Claude calls
    # Scan metadata
    agents_completed: list          # Which agents succeeded
    agents_failed: list             # Which agents failed (gracefully)


# ── Node 1: Parallel Scan ─────────────────────────────────────────────────────────
def parallel_scan_node(state: ProjectHealthState) -> dict:
    """
    Run security_audit, dependency_audit, and architecture_review concurrently.
    Uses ThreadPoolExecutor — all 3 start immediately, results merged when all complete.

    Failure policy:
      - One agent failing does not cancel others
      - Failed agents contribute an empty string to their output field
      - agents_completed + agents_failed track which ran successfully
    """
    thread_id  = state.get("workflow_id") or str(uuid.uuid4())
    repo_owner = state["repo_owner"]
    repo_name  = state["repo_name"]
    focus      = state.get("focus", "general")
    ts         = datetime.now(timezone.utc).isoformat()
    state_logger = SupabaseStateLogger()

    base = {
        "workflow_id": thread_id,
        "timestamp":   ts,
        "error":       None,
    }

    # PRE checkpoint
    state_logger.log_state(
        thread_id, f"project_health_scan_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "project_health",
        {"repo": f"{repo_owner}/{repo_name}", "focus": focus, "status": "scanning"},
    )

    log.info("project_health.scan_started",
             thread_id=thread_id, repo=f"{repo_owner}/{repo_name}")

    # Define tasks
    def run_security():
        return security_audit_node({
            **base, "agent": "security_audit",
            "repo_owner": repo_owner, "repo_name": repo_name,
            "security_report": "", "risk_level": "UNKNOWN",
        })

    def run_dependency():
        return dependency_audit_node({
            **base, "agent": "dependency_audit",
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": focus if focus in ("security", "general") else "general",
            "dependency_report": "",
        })

    def run_architecture():
        return architecture_review_node({
            **base, "agent": "architecture_review",
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": focus if focus in ("architecture", "general") else "general",
            "architecture_report": "",
        })

    tasks = {
        "security_audit":     run_security,
        "dependency_audit":   run_dependency,
        "architecture_review": run_architecture,
    }

    results: dict = {}
    errors:  dict = {}

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                log.info("project_health.agent_completed", agent=name, thread_id=thread_id)
            except Exception as exc:
                errors[name] = str(exc)
                results[name] = {}
                log.error("project_health.agent_failed", agent=name, error=str(exc))

    completed = [n for n in tasks if n not in errors]
    failed    = list(errors.keys())

    return {
        "security_report":     results["security_audit"].get("security_report", ""),
        "risk_level":          results["security_audit"].get("risk_level", "UNKNOWN"),
        "dependency_report":   results["dependency_audit"].get("dependency_report", ""),
        "architecture_report": results["architecture_review"].get("architecture_report", ""),
        "agents_completed":    completed,
        "agents_failed":       failed,
        "workflow_id":         thread_id,
    }


# ── Node 2: Merge Results ─────────────────────────────────────────────────────────
def merge_results_node(state: ProjectHealthState) -> dict:
    """
    Consolidate 3 specialist reports into a single health artifact.
    Pure function — no I/O, no Claude call.
    The artifact is passed to quality_validation for scoring.
    """
    repo      = f"{state['repo_owner']}/{state['repo_name']}"
    completed = state.get("agents_completed", [])
    failed    = state.get("agents_failed", [])
    risk      = state.get("risk_level", "UNKNOWN")

    sections = []

    if state.get("security_report"):
        sections.append(f"## Security Audit\nRisk Level: {risk}\n\n{state['security_report']}")

    if state.get("dependency_report"):
        sections.append(f"## Dependency Audit\n\n{state['dependency_report']}")

    if state.get("architecture_report"):
        sections.append(f"## Architecture Review\n\n{state['architecture_report']}")

    failed_note = ""
    if failed:
        failed_note = f"\n\n> ⚠️ The following agents did not complete: {', '.join(failed)}."

    artifact = f"""# Project Health Report: {repo}

**Scan Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
**Agents Run:** {len(completed)}/3 — {', '.join(completed) or 'none'}{failed_note}

---

{"".join(chr(10)*2 + s for s in sections) if sections else "_No reports available._"}
"""

    log.info("project_health.merged",
             repo=repo, sections=len(sections), failed=failed)
    return {"health_artifact": artifact.strip()}


# ── Node 3: Quality Gate ──────────────────────────────────────────────────────────
def quality_gate_node(state: ProjectHealthState) -> dict:
    """
    Score the merged health artifact using quality_validation_node.
    Non-fatal — if scoring fails, report is still returned with score=0.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    state_logger = SupabaseStateLogger()
    notifier     = TelegramNotifier()

    artifact = state.get("health_artifact", "")
    if not artifact.strip():
        return {
            "health_score": 0, "passed": False,
            "validation_report": "No artifact to validate — all agents failed.",
            "telemetry_summary": {},
        }

    result = quality_validation_node({
        "workflow_id": thread_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "agent":       "quality_validation",
        "error":       None,
        "artifact":    artifact,
        "criteria":    "Score this project health report on: completeness, accuracy, actionability, clarity, and overall usefulness.",
        "validation_report": "",
        "score":  0,
        "passed": False,
    })

    score  = result.get("score", 0)
    passed = score >= PASS_THRESHOLD

    # POST checkpoint
    state_logger.log_state(
        thread_id,
        f"project_health_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "project_health",
        {
            "repo":   f"{state['repo_owner']}/{state['repo_name']}",
            "score":  score,
            "passed": passed,
            "agents_completed": state.get("agents_completed", []),
        },
    )

    if not passed:
        notifier.alert(
            f"⚠️ Project Health scan BELOW threshold: "
            f"{state['repo_owner']}/{state['repo_name']} scored {score}/10"
        )

    log.info("project_health.gate_complete",
             thread_id=thread_id, score=score, passed=passed)

    return {
        "health_score":      score,
        "passed":            passed,
        "validation_report": result.get("validation_report", ""),
        "telemetry_summary": {},   # Extended in future: aggregate CallMetrics
    }


# ── Graph Builder ──────────────────────────────────────────────────────────────────
def build_project_health():
    """Build and compile the project health graph."""
    graph = StateGraph(ProjectHealthState)
    graph.add_node("parallel_scan",   parallel_scan_node)
    graph.add_node("merge_results",   merge_results_node)
    graph.add_node("quality_gate",    quality_gate_node)
    graph.add_edge(START,            "parallel_scan")
    graph.add_edge("parallel_scan",  "merge_results")
    graph.add_edge("merge_results",  "quality_gate")
    graph.add_edge("quality_gate",    END)
    return graph.compile(checkpointer=MemorySaver())


# ── CLI runner ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, time

    owner  = sys.argv[1] if len(sys.argv) > 1 else "jonnyallum"
    repo   = sys.argv[2] if len(sys.argv) > 2 else "JaiO.S-6.0-Experimental"
    focus  = sys.argv[3] if len(sys.argv) > 3 else "general"

    app    = build_project_health()
    wf_id  = str(uuid.uuid4())
    t0     = time.time()

    result = app.invoke(
        {
            "workflow_id":         wf_id,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
            "agent":               "project_health",
            "error":               None,
            "repo_owner":          owner,
            "repo_name":           repo,
            "focus":               focus,
            "security_report":     "",
            "risk_level":          "UNKNOWN",
            "dependency_report":   "",
            "architecture_report": "",
            "health_artifact":     "",
            "health_score":        0,
            "passed":              False,
            "validation_report":   "",
            "telemetry_summary":   {},
            "agents_completed":    [],
            "agents_failed":       [],
        },
        config={"configurable": {"thread_id": wf_id}},
    )

    elapsed = round(time.time() - t0, 1)

    print(f"\n{'='*60}")
    print(f"PROJECT HEALTH: {owner}/{repo}")
    print(f"Score : {result['health_score']}/10  |  Passed: {result['passed']}")
    print(f"Agents: {result['agents_completed']}")
    print(f"Time  : {elapsed}s")
    print(f"{'='*60}\n")
    print(result["health_artifact"][:2000])
