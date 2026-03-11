"""
utils.checkpoints — lightweight checkpoint helper for batch 2–9 agents.

Writes PRE/POST snapshots to structlog. Non-fatal — never raises.
In Phase 2 this will write to the PostgresSaver; for now it's a no-op
logger so agents can run without a live DB connection.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()


def checkpoint(phase: str, thread_id: str, agent: str, data: dict) -> None:
    """Log a PRE or POST checkpoint snapshot. Never raises."""
    try:
        log.info(
            "checkpoint",
            phase=phase.upper(),
            thread_id=thread_id,
            agent=agent,
            **{k: str(v)[:120] for k, v in data.items()},
        )
    except Exception:
        pass  # checkpoints are non-fatal


__all__ = ["checkpoint"]
