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
from agents.data_extraction import DataExtractionState, data_extraction_node
from agents.quality_validation import QualityValidationState, quality_validation_node
from agents.brief_writer import BriefWriterState, brief_writer_node
from agents.code_reviewer import CodeReviewState, code_reviewer_node
from agents.dependency_audit import DependencyAuditState, dependency_audit_node
from agents.social_post_generator import SocialPostState, social_post_generator_node
from agents.supabase_intelligence import BrainIntelState, supabase_intelligence_node
from agents.monetisation_strategist import MonetisationState, monetisation_strategist_node
from agents.sales_conversion import SalesConversionState, sales_conversion_node
from agents.content_scaler import ContentScalerState, content_scaler_node
from agents.automation_architect import AutomationState, automation_architect_node
from agents.business_intelligence import BIReportState, business_intelligence_node
from agents.seo_specialist import SEOState, seo_specialist_node
from agents.competitor_monitor import CompetitorIntelState, competitor_monitor_node
from agents.email_architect import EmailSequenceState, email_architect_node
from agents.video_brief_writer import VideoBriefState, video_brief_writer_node
from agents.funnel_architect import FunnelState, funnel_architect_node
from agents.analytics_reporter import analytics_reporter_node, AnalyticsState
from agents.ad_copy_writer import ad_copy_writer_node, AdCopyState
from agents.launch_orchestrator import launch_orchestrator_node, LaunchState
from agents.case_study_writer import case_study_writer_node, CaseStudyState
from agents.knowledge_base_writer import knowledge_base_writer_node, KnowledgeBaseState
from agents.agent_builder import agent_builder_node, AgentBuilderState
from state.base import BaseState
from tools.notification_tools import TelegramNotifier

log = structlog.get_logger()

# ── Keyword routing table ─────────────────────────────────────────────────────────
ROUTING_RULES: dict[str, list[str]] = {
    "github_intelligence": [
        "github", "repo", "repository", "commit", "pull request", "pr",
        "issue", "branch", "contributor", "merge", "diff",
    ],
    "security_audit": [
        "security", "vulnerability", "audit", "access", "permission",
        "encrypt", "auth", "token", "secret", "cve", "exposure", "risk",
    ],
    "architecture_review": [
        "architecture", "design", "refactor", "pattern", "stack", "api",
        "component", "structure", "tech debt",
    ],
    "data_extraction": [
        "parse", "extract", "schema", "json", "csv", "format",
        "convert", "transform", "scrape",
    ],
    "quality_validation": [
        "quality", "validate", "check", "qa", "verify", "pass", "fail", "score",
    ],
    "brief_writer": [
        "brief", "proposal", "scope of work", "sow", "document",
        "discovery", "onboarding doc", "client report", "write brief",
    ],
    "code_reviewer": [
        "review code", "code review", "code quality", "feedback on code",
        "lint", "smell", "file review",
    ],
    "dependency_audit": [
        "dependency", "dependencies", "package", "requirements", "npm",
        "pip", "outdated", "licence", "license", "lockfile",
    ],
    "social_post_generator": [
        "social", "post", "facebook", "instagram", "caption",
        "broadcast", "hashtag", "fb post", "ig post",
    ],
    "supabase_intelligence": [
        "supabase", "brain", "shared brain", "agent data", "learnings",
        "chatroom", "who is", "which agent",
    ],
    "monetisation_strategist": [
        "monetis", "monetiz", "revenue", "pricing", "funnel", "mrr",
        "arr", "subscription", "upsell", "profit", "income", "earn",
    ],
    "sales_conversion": [
        "prospect", "close", "objection", "deal", "pipeline", "crm",
        "follow up", "pitch", "negotiate", "sales call", "cold",
    ],
    "content_scaler": [
        "content", "copy", "variant", "a/b", "caption", "headline",
        "ad copy", "email subject", "blog intro", "brand voice",
    ],
    "automation_architect": [
        "automat", "n8n", "workflow", "trigger", "webhook", "cron",
        "zapier", "make", "integration", "pipeline", "email sequence",
    ],
    "business_intelligence": [
        "kpi", "metric", "dashboard", "report", "analytics", "forecast",
        "trend", "performance", "revenue report", "bi ", "data analysis",
    ],
    "seo_specialist": [
        "seo", "search engine", "on-page", "meta tag", "schema markup",
        "keyword gap", "keyword rank", "serp", "google ranking", "canonical",
    ],
    "competitor_monitor": [
        "competitor", "rival", "intel", "competitive", "market analysis",
        "spy", "monitor", "what are competitors", "compare to", "scrape site",
    ],
    "email_architect": [
        "email sequence", "email campaign", "nurture sequence", "drip", "follow-up email",
        "onboarding email", "re-engagement", "cold email", "email series", "write emails",
    ],
    "video_brief_writer": [
        "video brief", "tiktok", "reels", "youtube short", "short-form video",
        "hook script", "video script", "film brief", "b-roll", "video content",
    ],
    "funnel_architect": [
        "conversion funnel", "sales funnel", "landing page strategy", "cro",
        "objection handling", "offer structure", "upsell map", "funnel design",
        "top of funnel", "bottom of funnel", "awareness funnel",
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
    role        = state["selected_role"]
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    repo_owner  = state.get("repo_owner") or "jonnyallum"
    repo_name   = state.get("repo_name")  or "JaiO.S-6.0-Experimental"
    task        = state["task"]
    base        = {
        "workflow_id": workflow_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "error":       None,
    }

    log.info("supervisor.executing", role=role, workflow_id=workflow_id)

    # ── Technical agents ──────────────────────────────────────────────────────────

    if role == "github_intelligence":
        r = github_intelligence_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "query": task, "intelligence": "",
        })
        return {"result": r.get("intelligence", ""), "error": r.get("error")}

    elif role == "security_audit":
        r = security_audit_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "security_report": "", "risk_level": "UNKNOWN",
        })
        return {"result": r.get("security_report", ""), "error": r.get("error")}

    elif role == "architecture_review":
        r = architecture_review_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": "general", "architecture_report": "",
        })
        return {"result": r.get("architecture_report", ""), "error": r.get("error")}

    elif role == "data_extraction":
        r = data_extraction_node({
            **base, "agent": role,
            "source_text": task, "target_schema": "{}", "extracted_data": "",
        })
        return {"result": r.get("extracted_data", ""), "error": r.get("error")}

    elif role == "quality_validation":
        r = quality_validation_node({
            **base, "agent": role,
            "artifact": task, "criteria": "",
            "validation_report": "", "score": 0, "passed": False,
        })
        return {"result": r.get("validation_report", ""), "error": r.get("error")}

    elif role == "brief_writer":
        r = brief_writer_node({
            **base, "agent": role,
            "client_name": repo_owner, "brief_type": "proposal",
            "context": task,
            "goal": "Create a professional brief based on the provided context.",
            "budget_hint": "", "timeline_hint": "", "brief": "",
        })
        return {"result": r.get("brief", ""), "error": r.get("error")}

    elif role == "code_reviewer":
        tokens     = task.split()
        file_paths = [t.strip(",;") for t in tokens if ("/" in t or "." in t) and len(t) > 3]
        r = code_reviewer_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "file_paths": file_paths, "focus": "general", "code_review": "",
        })
        return {"result": r.get("code_review", ""), "error": r.get("error")}

    elif role == "dependency_audit":
        r = dependency_audit_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": "general", "dependency_report": "",
        })
        return {"result": r.get("dependency_report", ""), "error": r.get("error")}

    elif role == "social_post_generator":
        r = social_post_generator_node({
            **base, "agent": role,
            "brief": task, "platform": "facebook", "tone": "professional",
            "hashtags": "#JaiOS6 #JonnyAI", "publish": False,
            "image_url": None, "post_copy": {}, "published": False, "post_ids": {},
        })
        copy = r.get("post_copy", {})
        return {"result": copy.get("facebook") or copy.get("instagram") or "",
                "error": r.get("error")}

    elif role == "supabase_intelligence":
        r = supabase_intelligence_node({
            **base, "agent": role,
            "query": task, "focus": "general", "intelligence": "",
        })
        return {"result": r.get("intelligence", ""), "error": r.get("error")}

    # ── Business intelligence agents ──────────────────────────────────────────────

    elif role == "monetisation_strategist":
        r = monetisation_strategist_node({
            **base, "agent": role,
            "client_name": repo_owner,
            "business_context": task,
            "current_revenue": "",
            "goals": "Maximise revenue and build sustainable growth.",
            "constraints": "",
            "strategy": "",
        })
        return {"result": r.get("strategy", ""), "error": r.get("error")}

    elif role == "sales_conversion":
        r = sales_conversion_node({
            **base, "agent": role,
            "prospect_name": repo_owner,
            "company": repo_name,
            "deal_stage": "engaged",
            "context": task,
            "objections": "",
            "close_strategy": "",
        })
        return {"result": r.get("close_strategy", ""), "error": r.get("error")}

    elif role == "content_scaler":
        r = content_scaler_node({
            **base, "agent": role,
            "topic": task,
            "brand_voice": "professional, clear, results-first",
            "platform": "linkedin",
            "variant_count": 3,
            "cta": "",
            "variants": [],
        })
        variants = r.get("variants", [])
        return {"result": "\n\n---\n\n".join(variants), "error": r.get("error")}

    elif role == "automation_architect":
        r = automation_architect_node({
            **base, "agent": role,
            "workflow_description": task,
            "tools_available": "n8n, Resend, Supabase, OpenAI",
            "trigger_type": "webhook",
            "complexity": "medium",
            "automation_spec": "",
        })
        return {"result": r.get("automation_spec", ""), "error": r.get("error")}

    elif role == "business_intelligence":
        r = business_intelligence_node({
            **base, "agent": role,
            "client_name": repo_owner,
            "kpi_data": task,
            "period": "Current period",
            "goals": "",
            "context": "",
            "bi_report": "",
        })
        return {"result": r.get("bi_report", ""), "error": r.get("error")}


    elif role == "seo_specialist":
        r = seo_specialist_node({
            **base, "agent": role,
            "url": task, "page_content": task,
            "target_keywords": "", "business_context": "", "focus": "general",
            "seo_report": "",
        })
        return {"result": r.get("seo_report", ""), "error": r.get("error")}

    elif role == "competitor_monitor":
        r = competitor_monitor_node({
            **base, "agent": role,
            "competitor_url": task.split()[0] if task.startswith("http") else "https://example.com",
            "our_context": task, "focus": "general", "intel_report": "",
        })
        return {"result": r.get("intel_report", ""), "error": r.get("error")}

    elif role == "email_architect":
        r = email_architect_node({
            **base, "agent": role,
            "sequence_goal": "nurture", "audience": task,
            "product": "Our product/service", "num_emails": 3,
            "tone": "professional", "from_name": "The Team",
            "email_sequence": "", "email_count": 0,
        })
        return {"result": r.get("email_sequence", ""), "error": r.get("error")}

    elif role == "video_brief_writer":
        r = video_brief_writer_node({
            **base, "agent": role,
            "topic": task, "platform": "general", "duration_seconds": 60,
            "hook_style": "direct", "cta": "Follow for more",
            "brand_context": "", "video_brief": "",
        })
        return {"result": r.get("video_brief", ""), "error": r.get("error")}

    elif role == "funnel_architect":
        r = funnel_architect_node({
            **base, "agent": role,
            "product": task, "audience": "Our target customer",
            "funnel_stage": "consideration", "traffic_source": "organic_search",
            "avg_order_value": "unknown", "current_conversion": "unknown",
            "funnel_spec": "",
        })
        return {"result": r.get("funnel_spec", ""), "error": r.get("error")}
    else:
        return {"result": f"Role '{role}' is not wired into the supervisor.", "error": None}


def build_supervisor():
    """Build and compile the supervisor graph."""
    graph = StateGraph(SupervisorState)
    graph.add_node("route",   route_node)
    graph.add_node("execute", execute_node)
    graph.add_edge(START,     "route")
    graph.add_edge("route",   "execute")
    graph.add_edge("execute",  END)
    return graph.compile(checkpointer=MemorySaver())


if __name__ == "__main__":
    import sys

    task  = sys.argv[1] if len(sys.argv) > 1 else "Review the architecture of JaiO.S-6.0-Experimental"
    owner = sys.argv[2] if len(sys.argv) > 2 else "jonnyallum"
    repo  = sys.argv[3] if len(sys.argv) > 3 else "JaiO.S-6.0-Experimental"

    app    = build_supervisor()
    wf_id  = str(uuid.uuid4())
    result = app.invoke(
        {
            "workflow_id": wf_id,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "agent":       "orchestrator",
            "error":       None,
            "task":        task,
            "repo_owner":  owner,
            "repo_name":   repo,
            "selected_role": "",
            "result":      "",
        },
        config={"configurable": {"thread_id": wf_id}},
    )

    print(f"\n=== Supervisor Result ===")
    print(f"Role selected : {result['selected_role']}")
    print(f"\n{result['result'][:1000]}")
