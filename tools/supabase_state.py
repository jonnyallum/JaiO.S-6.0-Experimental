"""Write workflow state to Supabase graph_state table.

Used by graphs to manually checkpoint state after each run.
No direct PostgreSQL connection needed — uses Supabase REST API.
"""

import os
import json
import uuid
import urllib.request
from typing import Optional

# Support both ANTIGRAVITY_BRAIN_* and generic SUPABASE_* env vars
SUPABASE_URL = os.getenv("ANTIGRAVITY_BRAIN_URL") or os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("ANTIGRAVITY_BRAIN_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
)


def persist_state(workflow_id: str, state: dict, checkpoint_id: str = "") -> bool:
    """Write a state snapshot to Supabase graph_state table.

    Returns True on success, False if not configured or request failed.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[supabase_state] Not configured — set ANTIGRAVITY_BRAIN_URL + ANTIGRAVITY_BRAIN_SERVICE_ROLE_KEY")
        return False

    if not checkpoint_id:
        checkpoint_id = str(uuid.uuid4())

    # state_json must be serialisable — strip non-serialisable values
    try:
        safe_state = json.loads(json.dumps(state, default=str))
    except Exception:
        safe_state = {"raw": str(state)}

    payload = json.dumps({
        "workflow_id": workflow_id,
        "state_json": safe_state,
        "checkpoint_id": checkpoint_id,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/graph_state",
            data=payload,
            method="POST",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            success = r.status in (200, 201)
            if success:
                print(f"[supabase_state] ✅ Persisted workflow={workflow_id} checkpoint={checkpoint_id}")
            return success
    except Exception as e:
        print(f"[supabase_state] ❌ persist failed: {e}")
        return False


def load_state(workflow_id: str) -> Optional[dict]:
    """Load the most recent state snapshot for a workflow_id."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/graph_state"
            f"?workflow_id=eq.{workflow_id}&order=created_at.desc&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
            if rows:
                return rows[0]["state_json"]
    except Exception as e:
        print(f"[supabase_state] ❌ load failed: {e}")
    return None
