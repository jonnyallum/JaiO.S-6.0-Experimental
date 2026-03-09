"""
Supabase State Logger — writes workflow state snapshots to graph_state table.
Provides visibility into every agent execution via the Supabase dashboard.
Separate from LangGraph's internal checkpointer (MemorySaver in Phase 1).
"""
from datetime import datetime, timezone
from typing import Optional

import structlog
from supabase import create_client, Client

from config.settings import settings

log = structlog.get_logger()


class SupabaseStateLogger:
    """
    Logs state snapshots to the graph_state table after each agent node.
    Non-fatal: write failures are logged but never crash the workflow.
    """

    def __init__(self):
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.antigravity_brain_url,
                settings.antigravity_brain_service_role_key,
            )
        return self._client

    def log_state(
        self,
        workflow_id: str,
        checkpoint_id: str,
        agent: str,
        state: dict,
    ) -> bool:
        """
        Write a state snapshot to graph_state.
        Returns True on success. Always non-fatal.
        """
        try:
            self.client.table("graph_state").insert(
                {
                    "workflow_id": workflow_id,
                    "checkpoint_id": checkpoint_id,
                    "agent": agent,
                    "state_json": state,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
            log.debug(
                "state_logged",
                workflow_id=workflow_id,
                checkpoint_id=checkpoint_id,
                agent=agent,
            )
            return True
        except Exception as exc:
            log.warning(
                "state_log_failed",
                error=str(exc),
                workflow_id=workflow_id,
                checkpoint_id=checkpoint_id,
            )
            return False

    def get_workflow_history(self, workflow_id: str) -> list[dict]:
        """Fetch all state snapshots for a workflow, ordered by time."""
        try:
            result = (
                self.client.table("graph_state")
                .select("*")
                .eq("workflow_id", workflow_id)
                .order("created_at", desc=False)
                .execute()
            )
            return result.data
        except Exception as exc:
            log.error("state_fetch_failed", error=str(exc), workflow_id=workflow_id)
            return []

    def get_recent_workflows(self, limit: int = 20) -> list[dict]:
        """Fetch most recent workflow entries for dashboard."""
        try:
            result = (
                self.client.table("graph_state")
                .select("workflow_id, agent, created_at, state_json")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data
        except Exception as exc:
            log.error("recent_workflows_fetch_failed", error=str(exc))
            return []
