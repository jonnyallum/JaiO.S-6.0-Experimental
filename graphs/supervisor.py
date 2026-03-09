"""
Supervisor Graph — Orchestrator routing layer.

Routes tasks to specialist skill nodes based on keyword classification.
Pattern: START → route → execute_skill → END

Persona for the orchestrator is resolved via personas/config.py (role: orchestrator).
All routing logic is role-based — no persona names hardcoded.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.github_intelligence import GitHubIntelState, github_intelligence_node
from agents.security_audit import SecurityAuditState, security_audit_node
from agents.architecture_review import ArchitectureReviewState, architecture_review_node
from state.base import BaseState
from tools.notification_tools import TelegramNotifier

log = structlog.get_logger()

# Keyword routing table — extend as new skills are added
ROUTING_RULES: dict[str, list[str]] = {
    "github_intelligence": [
        "github", "repo", "repository", "commit", "pull request", "pr",
        "issue", "branch", "contributor", "merge", "diff", "code",
    ],
    "security_audit": [
        "security", "vulnerability", "audit", "access", "permission",
        "encrypt", "auth", "token", "secret", "cve", "exposure", "risk",
    ],
    "architecture_review": [
        "architecture", "design", "refactor", "pattern", "stack", "api",
        "component", "structure", "dependency", "tech debt", "review",
    ],
    "data_extraction": [
        "parse", "extract", "schema", "data", "json", "csv", "format",
        "convert", "transform", "scrape",
    ],
    "quality_validation": [
        "quality", "test", "validate", "check", "qa", "verify",
        "pass", "fail", "score",
    ],
}


class SupervisorState(BaseState):
    task: str
    repo_owner: Optional[str]
    repo_name: Optional[str]
    selected_role: str
    result: str


def _classify_task(task: str) -> str:
    """Classify task to role by keyword scoring. Defaults to github_intelligence."""
    task_lower = task.lower()
    scores = {role: 0 for role in ROUTING_RULES}
    for role, keywords in ROUTING_RULES.items():
        for kw in keywords:
            if kw in task_lower:
                scores[role] += 1
    best = max(scores, key=lambda r: scores[r])
    return best if scores[best] > 0 else "github_intelligence"


def route_node(state: SupervisorState) -> dict:
    """Classify the task and select the best skill."""
    selected = _classify_task(state["task"])
    log.info("supervisor.routing", selected=selected, task_preview=state["task"][:80])
    return {"selected_role": selected}


def execute_node(state: SupervisorState) -> dict:
    """Dispatch to the selected skill node."""
    role = state["selected_role"]
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    repo_owner = state.get("repo_owner") or "jonnyallum"
    repo_name = state.get("repo_name") or "JaiO.S-6.0-Experimental"
    base = {
        "workflow_id": workflow_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    log.info("supervisor.executing", role=role, workflow_id=workflow_id)

    if role == "github_intelligence":
        result = github_intelligence_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "query": state["task"], "intelligence": "",
        })
        return {"result": result.get("intelligence", ""), "error": result.get("error")}

    elif role == "security_audit":
        result = security_audit_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "security_report": "", "risk_level": "UNKNOWN",
        })
        return {"result": result.get("security_report", ""), "error": result.get("error")}

    elif role == "architecture_review":
        result = architecture_review_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": "general", "architecture_report": "",
        })
        return {"result": result.get("architecture_report", ""), "error": result.get("error")}

    else:
        return {
            "result": f"Role '{role}' not yet wired into supervisor. Add in Phase 2.",
            "error": None,
        }


def build_supervisor():
    """Build and compile the supervisor graph."""
    graph = StateGraph(SupervisorState)
    graph.add_node("route", route_node)
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=MemorySaver())


if __name__ == "__main__":
    import sys

    task = sys.argv[1] if len(sys.argv) > 1 else "Review the architecture of JaiO.S-6.0-Experimental"
    owner = sys.argv[2] if len(sys.argv) > 2 else "jonnyallum"
    repo = sys.argv[3] if len(sys.argv) > 3 else "JaiO.S-6.0-Experimental"

    app = build_supervisor()
    wf_id = str(uuid.uuid4())
    result = app.invoke(
        {
            "workflow_id": wf_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "orchestrator",
            "error": None,
            "task": task,
            "repo_owner": owner,
            "repo_name": repo,
            "selected_role": "",
            "result": "",
        },
        config={"configurable": {"thread_id": wf_id}},
    )

    print(f"\n=== Supervisor Result ===")
    print(f"Role selected : {result['selected_role']}")
    print(f"\n{result['result'][:1000]}")
