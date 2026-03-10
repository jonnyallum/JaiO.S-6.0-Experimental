"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOL : telemetry
PURPOSE : Track Claude API call cost + latency per agent per workflow.

Design (@langraph doctrine):
  - Zero-impact on agent correctness — all operations non-fatal
  - Always emits to structlog (no external deps required)
  - Optionally persists to Supabase graph_state with checkpoint prefix "telemetry_"
  - CallMetrics is mutable: call .start() before, .record(response) after

Usage in agents:
    from tools.telemetry import CallMetrics

    metrics = CallMetrics(workflow_id=thread_id, agent=ROLE)
    metrics.start()
    response = client.messages.create(...)
    metrics.record(response)
    metrics.log()       # always — writes to structlog
    metrics.persist()   # optional — writes to Supabase

Cost table (USD, 2026-03):
    claude-sonnet-4-6 : $3.00 input / $15.00 output per 1M tokens
    claude-opus-4-6   : $15.00 input / $75.00 output per 1M tokens
    claude-haiku-4-5  : $0.25 input / $1.25 output per 1M tokens
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import time
from datetime import datetime, timezone
from typing import Optional

import structlog

log = structlog.get_logger()

# ── Cost constants (USD per token, 2026-03) ──────────────────────────────────────
_COST_TABLE: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":            {"input": 3.00  / 1_000_000, "output": 15.00 / 1_000_000},
    "claude-opus-4-6":              {"input": 15.00 / 1_000_000, "output": 75.00 / 1_000_000},
    "claude-haiku-4-5-20251001":    {"input": 0.25  / 1_000_000, "output": 1.25  / 1_000_000},
    "claude-haiku-4-5":             {"input": 0.25  / 1_000_000, "output": 1.25  / 1_000_000},
}
_DEFAULT_MODEL = "claude-sonnet-4-6"


class CallMetrics:
    """
    Captures cost + latency for a single Claude API call.

    Lifecycle:
      1. metrics = CallMetrics(workflow_id, agent)
      2. metrics.start()            ← call just before messages.create()
      3. response = client.messages.create(...)
      4. metrics.record(response)   ← call immediately after
      5. metrics.log()              ← emit to structlog (always)
      6. metrics.persist()          ← write to Supabase (optional)
    """

    def __init__(self, workflow_id: str, agent: str):
        self.workflow_id   = workflow_id
        self.agent         = agent
        self.model         = _DEFAULT_MODEL
        self.input_tokens  = 0
        self.output_tokens = 0
        self.cost_usd      = 0.0
        self.latency_ms    = 0
        self.timestamp     = datetime.now(timezone.utc).isoformat()
        self._start_ns     = 0

    def start(self) -> None:
        """Start the latency timer. Call immediately before messages.create()."""
        self._start_ns = time.perf_counter_ns()

    def record(self, response) -> None:
        """
        Populate metrics from an Anthropic MessageResponse.
        Safe to call even if response is malformed — never raises.
        """
        try:
            self.latency_ms = (time.perf_counter_ns() - self._start_ns) // 1_000_000
            self.model = getattr(response, "model", _DEFAULT_MODEL)
            usage = getattr(response, "usage", None)
            if usage:
                self.input_tokens  = getattr(usage, "input_tokens",  0)
                self.output_tokens = getattr(usage, "output_tokens", 0)
            rates = _COST_TABLE.get(self.model, _COST_TABLE[_DEFAULT_MODEL])
            self.cost_usd = (
                self.input_tokens  * rates["input"] +
                self.output_tokens * rates["output"]
            )
        except Exception as exc:
            log.debug("telemetry.record_failed", error=str(exc))

    def to_dict(self) -> dict:
        return {
            "workflow_id":   self.workflow_id,
            "agent":         self.agent,
            "model":         self.model,
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd":      round(self.cost_usd, 6),
            "latency_ms":    self.latency_ms,
            "timestamp":     self.timestamp,
        }

    def log(self) -> None:
        """Emit call metrics to structlog. Always non-fatal."""
        try:
            log.info("claude_call", **self.to_dict())
        except Exception:
            pass

    def persist(self) -> None:
        """
        Write metrics to Supabase graph_state as a telemetry checkpoint.
        Non-fatal — silently skipped if Supabase is unavailable.
        """
        try:
            from tools.supabase_tools import SupabaseStateLogger
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            SupabaseStateLogger().log_state(
                workflow_id   = self.workflow_id,
                checkpoint_id = f"telemetry_{self.agent}_{ts}",
                agent         = self.agent,
                state         = self.to_dict(),
            )
        except Exception as exc:
            log.debug("telemetry.persist_failed", error=str(exc))


def session_summary(metrics_list: list["CallMetrics"]) -> dict:
    """
    Aggregate multiple CallMetrics into a session summary.
    Useful at the end of a compound workflow to report total cost + latency.

    Returns:
        {
          "total_calls":     int,
          "total_tokens":    int,
          "total_cost_usd":  float,
          "total_latency_ms": int,
          "by_agent":        dict[str, dict]
        }
    """
    by_agent: dict[str, dict] = {}
    for m in metrics_list:
        if m.agent not in by_agent:
            by_agent[m.agent] = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "latency_ms": 0}
        by_agent[m.agent]["calls"]      += 1
        by_agent[m.agent]["tokens"]     += m.input_tokens + m.output_tokens
        by_agent[m.agent]["cost_usd"]   += m.cost_usd
        by_agent[m.agent]["latency_ms"] += m.latency_ms

    return {
        "total_calls":      len(metrics_list),
        "total_tokens":     sum(m.input_tokens + m.output_tokens for m in metrics_list),
        "total_cost_usd":   round(sum(m.cost_usd for m in metrics_list), 6),
        "total_latency_ms": sum(m.latency_ms for m in metrics_list),
        "by_agent":         by_agent,
    }
