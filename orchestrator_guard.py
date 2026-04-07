"""
orchestrator_guard.py — jAIlbreakOS Orchestrator Entry Point Guard

Fixes:
  1. 'expected_behaviour is required' PERMANENT error
  2. Routes meta/boot/status briefs directly — no pipeline needed
  3. Integrates BrainBridge for cross-system delegation

Usage — add to TOP of your orchestrator entry point:

    from orchestrator_guard import OrchestratorGuard, safe_pipeline_monitor_kwargs
    guard = OrchestratorGuard()

    def run_job(brief, workflow_id, **kwargs):
        # 1. Intercept meta briefs
        result = guard.intercept(brief, workflow_id)
        if result:
            return result

        # 2. Ensure expected_behaviour never missing
        kwargs["expected_behaviour"] = guard.get_expected_behaviour(brief, kwargs.get("expected_behaviour"))

        # ... rest of LangGraph pipeline unchanged ...
"""

from brain_bridge import BrainBridge


class OrchestratorGuard:

    def intercept(self, brief: str, workflow_id: str) -> dict | None:
        """
        Call BEFORE role selection. Returns result dict if handled, else None.
        """
        if not BrainBridge.is_meta_brief(brief):
            return None

        report = BrainBridge.format_boot_report(workflow_id)

        BrainBridge.post_chatroom(
            agent_id="vigil",
            message=f"**Boot/status check complete**\n\n{report[:400]}",
            message_type="system",
            metadata={"job_id": workflow_id, "auto": True}
        )

        # Update job to complete in Brain DB
        BrainBridge.update_job_status(
            job_id=workflow_id,
            status="complete",
            output=report,
        )

        return {
            "workflow_id":   workflow_id,
            "agent":         "orchestrator",
            "error":         None,
            "selected_role": "boot_reporter",
            "result":        report,
            "pipeline":      None,
        }

    def get_expected_behaviour(self, brief: str, provided: str | None = None) -> str:
        """
        Returns expected_behaviour — derives it if not provided.
        Prevents PERMANENT: expected_behaviour is required.
        """
        if provided:
            return provided
        return BrainBridge.derive_expected_behaviour(brief)

    def classify(self, brief: str) -> str:
        return BrainBridge.classify(brief)

    def should_delegate_to_orchestra(self, brief: str) -> bool:
        return BrainBridge.classify(brief) == "orchestra"


def safe_pipeline_monitor_kwargs(brief: str, kwargs: dict) -> dict:
    """
    Drop-in wrapper — ensures expected_behaviour is always populated
    before calling the pipeline_monitor role handler.

    Usage:
        from orchestrator_guard import safe_pipeline_monitor_kwargs
        kwargs = safe_pipeline_monitor_kwargs(brief, kwargs)
        result = run_pipeline_monitor(**kwargs)
    """
    if not kwargs.get("expected_behaviour"):
        kwargs["expected_behaviour"] = BrainBridge.derive_expected_behaviour(brief)
    return kwargs

# ── LANGGRAPH AGENT TOOL ─────────────────────────────────────────────────────

def get_chatroom_tool():
    """
    Returns a LangChain/LangGraph compatible tool that agents can use 
    to post progress directly to the team chatroom.
    
    Usage:
        from orchestrator_guard import get_chatroom_tool
        tools = [get_chatroom_tool(), other_tool, ...]
        agent.bind_tools(tools)
    """
    try:
        from langchain_core.tools import tool
        
        @tool
        def post_to_chatroom(agent_name: str, message: str) -> str:
            """
            Use this tool to post progress updates, findings, or blockers to the team chatroom.
            This keeps the Maestro (Conductor) and other agents informed of your work inside the pipeline.
            Make it concise, professional, and highlight key discoveries or decisions.
            """
            try:
                BrainBridge.post_chatroom(
                    agent_id=agent_name.lower(),
                    message=message,
                    message_type="agent_update",
                    metadata={"source": "langgraph_agent"}
                )
                return "Successfully posted to the team chatroom."
            except Exception as e:
                return f"Failed to post to chatroom: {str(e)}"
                
        return post_to_chatroom
    except ImportError:
        # Fallback if langchain isn't directly available or imported differently
        def post_to_chatroom_fallback(agent_name: str, message: str) -> str:
            BrainBridge.post_chatroom(
                agent_id=agent_name.lower(),
                message=message,
                message_type="agent_update",
                metadata={"source": "langgraph_fallback"}
            )
            return "Posted to chatroom."
        return post_to_chatroom_fallback
