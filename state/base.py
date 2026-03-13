"""Base state shared by all Jai.OS 6.0 workflow graphs."""
from typing import Optional
from typing_extensions import TypedDict


class BaseState(TypedDict):
    """Minimal shared state every workflow carries."""
    workflow_id: str       # UUID identifying this execution
    timestamp: str         # ISO-8601 start time
    agent: str             # Active agent handle (e.g. "hugo")
    error: Optional[str]   # Set on failure; None on success
    client_id: Optional[str]   # Client/org this task belongs to
    project_id: Optional[str]  # Project within the client
