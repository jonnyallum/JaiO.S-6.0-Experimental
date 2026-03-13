"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : agent_builder
 SKILL : Agent Builder — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : agent_role (str — snake_case identifier),
               agent_purpose (str — one-sentence capability),
               domain (str — specialisation domain),
               phase1_description (str — what Phase 1 pure function does),
               example_inputs (str — description of typical inputs),
               example_outputs (str — description of expected outputs)
 Output keys : agent_code (str — complete Python agent file),
               agent_filename (str), spec_checklist (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass generation.
 The full 19-point @langraph spec is embedded in this node's prompt
 so every agent it builds is compliant from day one.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid domain (ValueError), agent_role not snake_case,
               empty phase1_description or example_inputs
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: agent_role, domain, spec version
 POST — logged after success: code char count, spec_checklist pass rate

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import re

import anthropic
import structlog
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona
from tools.supabase_tools import SupabaseStateLogger

# ── Identity ──────────────────────────────────────────────────────────────────
log = structlog.get_logger()

ROLE = "agent_builder"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 4000   # full agent code needs maximum token budget

# ── Spec version ──────────────────────────────────────────────────────────────
SPEC_VERSION = "langraph-v1.19"   # bump when spec changes

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_DOMAINS = {
    "marketing", "analytics", "content", "development", "operations",
    "research", "sales", "design", "finance", "security", "automation", "general",
}

# ── The 19-point @langraph compliance spec (source of truth) ──────────────────
# This is the canonical spec embedded in the node itself.
# Any agent built by agent_builder must satisfy all 19 points.
# When the spec changes, update SPEC_VERSION and this dict.
_LANGRAPH_SPEC: dict[str, str] = {
    "01_docstring_block":      "Module-level docstring with ━━━ header and footer",
    "02_node_contract":        "Node Contract section with input/output keys",
    "03_loop_policy":          "Loop Policy section with explicit ceiling constants",
    "04_failure_discrimination": "Failure Discrimination section with PERMANENT/TRANSIENT/UNEXPECTED",
    "05_checkpoint_semantics": "Checkpoint Semantics section with PRE/POST descriptions",
    "06_callmetrics_mention":  "CallMetrics mentioned in docstring Side effects",
    "07_persona_note":         "Persona note: no names hardcoded, injected at runtime",
    "08_phase1_separation":    "Explicit '# ── Phase 1' comment label in code",
    "09_phase2_separation":    "Explicit '# ── Phase 2' comment label in code",
    "10_max_retries":          "MAX_RETRIES named constant defined at module level",
    "11_max_tokens":           "MAX_TOKENS named constant defined at module level",
    "12_valid_set":            "At least one VALID_* set for input validation",
    "13_pre_checkpoint":       "checkpoint('PRE', ...) call before Claude",
    "14_post_checkpoint":      "checkpoint('POST', ...) call after success",
    "15_metrics_start":        "metrics.start() called before client.messages.create()",
    "16_metrics_record":       "metrics.record(response) called after create()",
    "17_metrics_log":          "metrics.log() called after success",
    "18_metrics_persist":      "metrics.persist() called after success",
    "19_build_prompt":         "Named _build_prompt() pure function for prompt construction",
}

# ── State ──────────────────────────────────────────────────────────────────────
class AgentBuilderState(BaseState):
    # Inputs
    agent_role:          str   # snake_case role identifier
    agent_purpose:       str   # one-sentence capability description
    domain:              str   # specialisation domain
    phase1_description:  str   # what Phase 1 pure function computes
    example_inputs:      str   # description of typical inputs
    example_outputs:     str   # description of expected outputs
    thread_id:           str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    spec_checklist: dict  # 19-point compliance map (owner: this node)

    # Outputs
    agent_code:     str   # complete Python agent file content (owner: this node)
    agent_filename: str   # suggested filename e.g. analytics_reporter.py (owner: this node)
    error:          str   # failure reason if any (owner: this node)


# ── Phase 1 — pre-flight validation and spec scaffold (no Claude) ─────────────

_SNAKE_CASE_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def _validate_and_scaffold(agent_role: str, domain: str) -> tuple[dict, dict]:
    """
    Phase 1 — validate role name and return the spec checklist scaffold.
    Returns (spec_checklist, metadata). Pure function — no Claude.
    """
    if not _SNAKE_CASE_RE.match(agent_role):
        raise ValueError(f"agent_role '{agent_role}' must be snake_case (e.g. 'my_agent')")
    if domain not in VALID_DOMAINS:
        raise ValueError(f"domain '{domain}' not in {VALID_DOMAINS}")

    # Return the spec as a pre-flight checklist (values to be verified after generation)
    spec_checklist = {k: False for k in _LANGRAPH_SPEC}
    metadata = {
        "spec_version":   SPEC_VERSION,
        "spec_point_count": len(_LANGRAPH_SPEC),
    }
    return spec_checklist, metadata


def _verify_spec(code: str) -> dict[str, bool]:
    """
    Phase 1 — verify the generated code satisfies the 19-point spec.
    Returns {spec_key: bool}. Pure function — no Claude.
    """
    checks = {
        "01_docstring_block":        "━━━" in code,
        "02_node_contract":          "Node Contract" in code,
        "03_loop_policy":            "Loop Policy" in code,
        "04_failure_discrimination": "Failure Discrimination" in code,
        "05_checkpoint_semantics":   "Checkpoint Semantics" in code,
        "06_callmetrics_mention":    "CallMetrics" in code,
        "07_persona_note":           "personas/config.py" in code or "get_persona" in code,
        "08_phase1_separation":      "# ── Phase 1" in code,
        "09_phase2_separation":      "# ── Phase 2" in code,
        "10_max_retries":            "MAX_RETRIES" in code,
        "11_max_tokens":             "MAX_TOKENS" in code,
        "12_valid_set":              bool(re.search(r'VALID_\w+\s*=\s*\{', code)),
        "13_pre_checkpoint":         'checkpoint("PRE"' in code or "checkpoint('PRE'" in code,
        "14_post_checkpoint":        'checkpoint("POST"' in code or "checkpoint('POST'" in code,
        "15_metrics_start":          "metrics.start()" in code,
        "16_metrics_record":         "metrics.record(" in code,
        "17_metrics_log":            "metrics.log()" in code,
        "18_metrics_persist":        "metrics.persist()" in code,
        "19_build_prompt":           "_build_prompt(" in code and "def _build_prompt(" in code,
    }
    return checks


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    agent_role: str,
    agent_purpose: str,
    domain: str,
    phase1_description: str,
    example_inputs: str,
    example_outputs: str,
) -> str:
    """Pure function — assembles the agent scaffolding brief."""
    persona      = get_persona(ROLE)
    spec_entries = "\n".join(f"  {k}: {v}" for k, v in _LANGRAPH_SPEC.items())
    class_name   = "".join(w.title() for w in agent_role.split("_")) + "State"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} agent architect.

Build a complete, production-ready JaiOS 6 LangGraph skill node for:

Role          : {agent_role}
Purpose       : {agent_purpose}
Domain        : {domain}
Class name    : {class_name}
Phase 1       : {phase1_description}
Inputs        : {example_inputs}
Outputs       : {example_outputs}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 MANDATORY 19-POINT @LANGRAPH COMPLIANCE SPEC (ALL REQUIRED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{spec_entries}

ARCHITECTURE RULES:
1. Module-level docstring: ━━━ header/footer, all 5 contract sections, persona note
2. ROLE = "{agent_role}" at module level
3. MAX_RETRIES = 3, MAX_TOKENS = [appropriate for task] at module level
4. VALID_* sets covering all enum-like inputs — invalid values → ValueError → PERMANENT
5. Named data dicts (e.g. _PLATFORM_SPECS, _STAGE_MAP) for lookup tables
6. TypedDict state with owner comments on computed/output keys
7. Phase 1 pure function: _[verb]_[noun](inputs) → (result) — no Claude, independently testable
8. _build_prompt(*args) pure function — assembles prompt from Phase 1 outputs, calls get_persona(ROLE)
9. @retry decorated Phase 2 function: _[verb]_[noun](client, prompt, metrics) → str
   - metrics.start() before client.messages.create()
   - metrics.record(response) after create()
10. Node function {agent_role}_node(state: {class_name}) → {class_name}:
    - PERMANENT validation block first
    - Phase 1 section with "# ── Phase 1 ──" label
    - _build_prompt() call
    - checkpoint("PRE", ...) before claude = anthropic.Anthropic()
    - metrics = CallMetrics(thread_id, ROLE)
    - Phase 2 section with "# ── Phase 2 ──" label inside try/except
    - metrics.log() + metrics.persist() after success
    - checkpoint("POST", ...) after telemetry
    - Return full state dict
11. build_graph() → compiled StateGraph
12. Imports: anthropic, APIStatusError, StateGraph, END, retry decorators, TypedDict, checkpoint, CallMetrics, get_persona

IDENTITY RULES (CRITICAL):
- ZERO hardcoded names, nicknames, or "Antigravity" anywhere
- Only reference: JaiOS 6, get_persona(ROLE), personas/config.py

OUTPUT: The complete Python file content only. No markdown, no explanations."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _generate_agent(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


# ── Node ───────────────────────────────────────────────────────────────────────

def agent_builder_node(state: AgentBuilderState) -> AgentBuilderState:
    thread_id           = state.get("thread_id", "unknown")
    agent_role          = state.get("agent_role", "").strip()
    agent_purpose       = state.get("agent_purpose", "").strip()
    domain              = state.get("domain", "general").lower().strip()
    phase1_description  = state.get("phase1_description", "").strip()
    example_inputs      = state.get("example_inputs", "").strip()
    example_outputs     = state.get("example_outputs", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not agent_role:
        return {**state, "error": "PERMANENT: agent_role is required (snake_case)"}
    if not agent_purpose:
        return {**state, "error": "PERMANENT: agent_purpose is required"}
    if not phase1_description:
        return {**state, "error": "PERMANENT: phase1_description is required"}
    if not example_inputs:
        return {**state, "error": "PERMANENT: example_inputs is required"}

    # ── Phase 1 — validate role name and scaffold spec ────────────────────────
    try:
        spec_checklist, metadata = _validate_and_scaffold(agent_role, domain)
    except ValueError as exc:
        return {**state, "error": f"PERMANENT: {exc}"}

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(agent_role, agent_purpose, domain, phase1_description, example_inputs, example_outputs)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "agent_role": agent_role,
        "domain": domain,
        "spec_version": SPEC_VERSION,
        "spec_point_count": metadata["spec_point_count"],
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        raw_code = _generate_agent(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # Strip markdown code fences if Claude wrapped the output
    agent_code = raw_code.strip()
    if agent_code.startswith("```"):
        agent_code = re.sub(r'^```\w*\n?', '', agent_code)
        agent_code = re.sub(r'\n?```\s*$', '', agent_code)

    # ── Phase 1 (post-generation) — verify spec compliance ────────────────────
    spec_checklist = _verify_spec(agent_code)
    pass_count     = sum(1 for v in spec_checklist.values() if v)
    total          = len(spec_checklist)

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "agent_role":     agent_role,
        "code_chars":     len(agent_code),
        "spec_pass":      pass_count,
        "spec_total":     total,
        "spec_pct":       round((pass_count / total) * 100),
    })

    return {
        **state,
        "agent_code":     agent_code,
        "agent_filename": f"{agent_role}.py",
        "spec_checklist": spec_checklist,
        "error":          "" if pass_count == total else f"WARN: {total - pass_count} spec points missing — review spec_checklist",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentBuilderState)
    g.add_node("agent_builder", agent_builder_node)
    g.set_entry_point("agent_builder")
    g.add_edge("agent_builder", END)
    return g.compile()
