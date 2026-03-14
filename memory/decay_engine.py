"""
Memory Decay Engine — Smart aging for the Memory Spine.

Runs as a standalone script or imported for scheduled use.
Decay is importance-weighted: high-importance memories decay slower.

Usage:
    # As CLI
    python -m memory.decay_engine

    # From supervisor/scheduler
    from memory.decay_engine import run_decay_cycle
    report = run_decay_cycle()
"""
import logging
import time
from datetime import datetime, timezone, timedelta

from memory.connection import db_connection

log = logging.getLogger(__name__)


# ── Decay Configuration ──────────────────────────────────────────────────────
DECAY_CONFIG = {
    # Memories not accessed in X days start decaying
    "stale_threshold_days": 14,
    # Base decay rate per cycle (multiplied by inverse importance)
    "base_decay_rate": 0.90,      # 10% reduction per cycle
    # High-importance memories decay slower
    "importance_shield": True,     # importance > 0.8 → half decay rate
    # Archive threshold — memories below this are archived
    "archive_threshold": 0.15,
    # Poison threshold — memories accessed 0 times AND very old
    "poison_after_days": 90,
    # Max memories to process per cycle (safety limit)
    "batch_limit": 500,
}


def run_decay_cycle(config: dict | None = None) -> dict:
    """
    Execute one full decay cycle.

    Strategy:
    1. Find stale memories (not accessed recently)
    2. Apply importance-weighted decay factor reduction
    3. Archive memories that have decayed below threshold
    4. Flag ancient never-accessed memories as poisoned
    5. Return a health report

    Returns:
        dict with decay statistics
    """
    cfg = {**DECAY_CONFIG, **(config or {})}
    start = time.time()

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["stale_threshold_days"])
    poison_cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["poison_after_days"])

    decayed = 0
    archived = 0
    poisoned = 0
    shielded = 0

    with db_connection() as conn:
        with conn.cursor() as cur:
            # ── Step 1: Identify stale memories ──────────────────
            cur.execute(
                """
                SELECT id, agent_id, decay_factor, importance, access_count,
                       last_accessed_at, created_at
                FROM memories
                WHERE status = 'active'
                  AND deleted_at IS NULL
                  AND last_accessed_at < %s
                ORDER BY last_accessed_at ASC
                LIMIT %s
                """,
                (stale_cutoff, cfg["batch_limit"]),
            )
            stale_rows = cur.fetchall()

            for row in stale_rows:
                mem_id, agent_id, decay, importance, access_count, last_accessed, created = row

                # ── Step 2: Importance-weighted decay ────────────
                if cfg["importance_shield"] and importance and importance > 0.8:
                    # High-importance memories: half the decay rate
                    new_decay = max(decay * (1 - (1 - cfg["base_decay_rate"]) * 0.5), 0.01)
                    shielded += 1
                else:
                    new_decay = max(decay * cfg["base_decay_rate"], 0.01)

                cur.execute(
                    """
                    UPDATE memories
                    SET decay_factor = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (round(new_decay, 4), str(mem_id)),
                )
                decayed += 1

            # ── Step 3: Archive heavily decayed memories ─────────
            cur.execute(
                """
                UPDATE memories
                SET status = 'archived', updated_at = NOW()
                WHERE status = 'active'
                  AND deleted_at IS NULL
                  AND decay_factor < %s
                RETURNING id, agent_id
                """,
                (cfg["archive_threshold"],),
            )
            archived_rows = cur.fetchall()
            archived = len(archived_rows)

            for row in archived_rows:
                log.info(f"memory.archived id={row[0]} agent={row[1]}")

            # ── Step 4: Poison ancient never-accessed memories ───
            cur.execute(
                """
                UPDATE memories
                SET status = 'poisoned', updated_at = NOW()
                WHERE status = 'active'
                  AND deleted_at IS NULL
                  AND access_count = 0
                  AND created_at < %s
                RETURNING id, agent_id
                """,
                (poison_cutoff,),
            )
            poisoned_rows = cur.fetchall()
            poisoned = len(poisoned_rows)

            for row in poisoned_rows:
                log.info(f"memory.poisoned id={row[0]} agent={row[1]}")

    elapsed_ms = round((time.time() - start) * 1000, 1)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "stale_found": len(stale_rows) if 'stale_rows' in dir() else 0,
        "decayed": decayed,
        "shielded": shielded,
        "archived": archived,
        "poisoned": poisoned,
        "config": {
            "stale_days": cfg["stale_threshold_days"],
            "decay_rate": cfg["base_decay_rate"],
            "archive_at": cfg["archive_threshold"],
            "poison_after": cfg["poison_after_days"],
        },
    }

    log.info(
        f"memory.decay_cycle decayed={decayed} shielded={shielded} "
        f"archived={archived} poisoned={poisoned} elapsed_ms={elapsed_ms}"
    )
    return report


# ── Per-Agent Decay Report ────────────────────────────────────────────────────

def get_decay_report() -> dict:
    """
    Get current decay health across all agents.
    Returns per-agent breakdown of memory freshness.
    """
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    agent_id,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'active') AS active,
                    COUNT(*) FILTER (WHERE status = 'archived') AS archived,
                    COUNT(*) FILTER (WHERE status = 'poisoned') AS poisoned,
                    ROUND(AVG(decay_factor)::numeric, 3) AS avg_decay,
                    ROUND(AVG(importance)::numeric, 3) AS avg_importance,
                    ROUND(AVG(access_count)::numeric, 1) AS avg_accesses,
                    MIN(last_accessed_at) AS oldest_access,
                    MAX(last_accessed_at) AS newest_access
                FROM memories
                WHERE deleted_at IS NULL
                GROUP BY agent_id
                """
            )
            db_rows = cur.fetchall()

    # Create mapping from db
    db_stats = {}
    for row in db_rows:
        db_stats[row[0]] = {
            "total": row[1],
            "active": row[2],
            "archived": row[3],
            "poisoned": row[4],
            "avg_decay": float(row[5]) if row[5] else 1.0,
            "avg_importance": float(row[6]) if row[6] else 0.5,
            "avg_accesses": float(row[7]) if row[7] else 0,
            "oldest_access": row[8].isoformat() if row[8] else None,
            "newest_access": row[9].isoformat() if row[9] else None,
        }

    # 1. Fetch 93 canonical agents to prevent old persona bleedover
    try:
        from agents import __all__ as agent_exports
        canonical_agents = sorted([x.replace("_node", "") for x in agent_exports if x.endswith("_node")])
    except ImportError:
        canonical_agents = []

    # Include special operational agents that might hold memory
    system_agents = ["human_operator", "dashboard"]

    agents = []
    
    # Render all 93 canonical agents + system agents
    for agent_id in canonical_agents + system_agents:
        if agent_id in db_stats:
            stats = db_stats[agent_id]
            stats["agent_id"] = agent_id
            agents.append(stats)
        else:
            # Agent exists but has no memories yet
            agents.append({
                "agent_id": agent_id,
                "total": 0, "active": 0, "archived": 0, "poisoned": 0,
                "avg_decay": 1.0, "avg_importance": 0.5, "avg_accesses": 0.0,
                "oldest_access": None, "newest_access": None,
            })

    # Sort: active first, then alphabetic
    agents.sort(key=lambda x: (-x["active"], x["agent_id"]))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_count": len(agents),
        "agents": agents,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv(override=True)

    logging.basicConfig(level=logging.INFO)
    print("\n=== Memory Decay Engine ===\n")

    report = run_decay_cycle()
    print(json.dumps(report, indent=2))

    print("\n=== Per-Agent Health ===\n")
    health = get_decay_report()
    for a in health["agents"]:
        status = "🟢" if a["avg_decay"] > 0.7 else "🟡" if a["avg_decay"] > 0.3 else "🔴"
        print(
            f"  {status} {a['agent_id']:25s} "
            f"active={a['active']:3d}  archived={a['archived']:3d}  "
            f"decay={a['avg_decay']:.2f}  importance={a['avg_importance']:.2f}"
        )
    print(f"\n  Total agents with memories: {health['agent_count']}")
