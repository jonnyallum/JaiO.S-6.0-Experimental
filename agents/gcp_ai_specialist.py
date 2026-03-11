"""
GCP AI Platform Specialist — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), gcp_context (str), output_type (VALID_OUTPUT_TYPES), gcp_service (VALID_GCP_SERVICES)
    Outputs: gcp_spec (str), terraform_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/gcp_service → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before GCP architecture design
    POST — gcp_spec + terraform_output persisted after successful generation
"""

from __future__ import annotations

import re
from typing import TypedDict

import anthropic
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint

ROLE        = "gcp_ai_specialist"
MAX_RETRIES = 3
MAX_TOKENS  = 2800

VALID_OUTPUT_TYPES = {
    "agent_architecture", "vertex_pipeline", "adk_agent", "a2a_protocol",
    "terraform_config", "cloud_run_deploy", "bigquery_schema", "general",
}
VALID_GCP_SERVICES = {
    "vertex_ai", "agent_engine", "adk", "cloud_run", "bigquery",
    "cloud_functions", "pubsub", "gcs", "general",
}

# ── GCP AI Service Profiles ────────────────────────────────────────────────────
_SERVICE_PROFILES = {
    "vertex_ai": {
        "use_case":    "Model training, fine-tuning, batch prediction",
        "sdk":         "google-cloud-aiplatform",
        "auth":        "gcloud auth application-default login + GOOGLE_CLOUD_PROJECT env var",
        "pricing":     "Per compute-hour + prediction requests",
        "strengths":   ["Managed ML ops", "AutoML", "Model Garden (Gemini, Claude, Llama)"],
        "gotchas":     ["Region lock — model availability varies by region", "Quota limits on first use"],
    },
    "agent_engine": {
        "use_case":    "Managed agent deployment and orchestration at scale",
        "sdk":         "google-cloud-aiplatform (AgentEngine class)",
        "auth":        "Service account with roles/aiplatform.user",
        "pricing":     "Per agent-second + storage",
        "strengths":   ["Managed session state", "Auto-scaling", "Built-in tracing"],
        "gotchas":     ["ADK required for agent definition", "Limited to supported regions"],
    },
    "adk": {
        "use_case":    "Agent Development Kit — build multi-agent systems locally + deploy to Agent Engine",
        "sdk":         "google-adk (pip install google-adk)",
        "auth":        "GOOGLE_GENAI_USE_VERTEXAI=true + project/location env vars",
        "pricing":     "No ADK cost — billed on underlying Gemini/Vertex calls",
        "strengths":   ["LangGraph-compatible", "A2A protocol support", "Local dev → cloud deploy"],
        "gotchas":     ["Python 3.10+ required", "Tools must be decorated with @tool", "State via InMemorySessionService for local"],
    },
    "cloud_run": {
        "use_case":    "Containerised app/API deployment — serverless",
        "sdk":         "gcloud run deploy",
        "auth":        "Service account with roles/run.invoker",
        "pricing":     "Per request + CPU/memory seconds",
        "strengths":   ["Auto-scales to zero", "Any container", "HTTPS by default"],
        "gotchas":     ["Cold starts (up to 10s)", "No persistent disk", "Max 60min request timeout"],
    },
    "general": {
        "use_case":    "GCP general workload",
        "sdk":         "google-cloud-* SDKs",
        "auth":        "ADC (Application Default Credentials)",
        "pricing":     "Service-specific",
        "strengths":   ["Global infrastructure", "managed services", "IAM"],
        "gotchas":     ["IAM permissions are granular — least-privilege always"],
    },
}

_A2A_PROTOCOL_NOTES = """Agent-to-Agent (A2A) Protocol (Google, 2025):
- Each agent exposes an Agent Card at /.well-known/agent.json
- Tasks sent via HTTP POST /tasks with JSON-RPC 2.0 payload
- Streaming via SSE for long-running tasks
- Authentication: API key or OAuth2 bearer token
- ADK agents auto-expose A2A endpoints when deployed to Agent Engine"""

_ADK_BOILERPLATE = """from google.adk.agents import Agent
from google.adk.tools import tool

@tool
def my_tool(param: str) -> str:
    """Tool description — shown to the model."""
    return f"Result: {param}"

root_agent = Agent(
    name="my_agent",
    model="gemini-2.0-flash",
    description="Agent description",
    instruction="Agent system prompt",
    tools=[my_tool],
)"""


class GcpAiSpecialistState(TypedDict, total=False):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    task:             str
    gcp_context:      str
    output_type:      str
    gcp_service:      str
    gcp_spec:         str
    terraform_output: str


# ── Phase 1 — GCP Architecture (pure, no Claude) ──────────────────────────────
def _design_gcp_architecture(task: str, gcp_service: str, output_type: str) -> dict:
    """Returns gcp_data dict — pure lookup, no Claude."""
    profile    = _SERVICE_PROFILES.get(gcp_service, _SERVICE_PROFILES["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if "agent" in task_lower or "adk" in task_lower:
        flags.append("ADK agent: use google-adk, decorate tools with @tool, deploy via Agent Engine")
    if "a2a" in task_lower or "multi-agent" in task_lower:
        flags.append("A2A protocol: each agent needs Agent Card + /tasks endpoint")
    if "terraform" in task_lower or "iac" in task_lower:
        flags.append("IaC required: use google provider — run terraform plan before apply")
    if "bigquery" in task_lower or "analytics" in task_lower:
        flags.append("BigQuery: partition by date, cluster by high-cardinality filter columns")
    if "realtime" in task_lower or "stream" in task_lower:
        flags.append("Streaming: Pub/Sub → Dataflow → BigQuery is the standard GCP pattern")

    include_a2a = "a2a" in task_lower or "multi-agent" in task_lower or "agent" in task_lower
    include_adk = "adk" in task_lower or "agent" in task_lower

    return {
        "profile":       profile,
        "flags":         flags,
        "a2a_notes":     _A2A_PROTOCOL_NOTES if include_a2a else "",
        "adk_boilerplate": _ADK_BOILERPLATE if include_adk else "",
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude GCP Spec ──────────────────────────────────────────────────
def _build_gcp_prompt(state: GcpAiSpecialistState, gcp_data: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    ctx         = state.get("gcp_context", "")
    out_type    = state.get("output_type", "general")
    gcp_service = state.get("gcp_service", "general")
    profile     = gcp_data["profile"]

    flags_text = "
".join(f"  ⚡ {f}" for f in gcp_data["flags"]) or "  None detected"

    a2a_section = f"
A2A PROTOCOL REFERENCE:
{gcp_data['a2a_notes']}" if gcp_data["a2a_notes"] else ""
    adk_section = f"
ADK BOILERPLATE:
```python
{gcp_data['adk_boilerplate']}
```" if gcp_data["adk_boilerplate"] else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Design a production-grade {out_type} using GCP service: {gcp_service}.

SERVICE PROFILE:
  Use Case:  {profile['use_case']}
  SDK:       {profile['sdk']}
  Auth:      {profile['auth']}
  Pricing:   {profile['pricing']}
  Strengths: {', '.join(profile['strengths'])}
  Gotchas:   {', '.join(profile['gotchas'])}

DESIGN FLAGS:
{flags_text}
{a2a_section}
{adk_section}

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## GCP AI Spec: {out_type.replace('_',' ').title()} — {gcp_service}

### Architecture Overview
[Diagram-style description — services, data flow, auth boundaries]

### Implementation
```python
# Python SDK code — production ready
```

```terraform
# Terraform config (if IaC required)
```

### IAM & Security
[Service accounts, roles, least-privilege setup]

### Deployment Steps
```bash
# gcloud commands in order
```

### Cost Estimate
[Rough monthly cost at expected scale]

### Monitoring & Observability
[Cloud Monitoring alerts, logging setup, trace IDs]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_gcp_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-opus-4-6", max_tokens=MAX_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def gcp_ai_specialist_node(state: GcpAiSpecialistState) -> GcpAiSpecialistState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    out_type    = state.get("output_type", "general")
    gcp_service = state.get("gcp_service", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if gcp_service not in VALID_GCP_SERVICES:
        raise ValueError(f"PERMANENT: gcp_service '{gcp_service}' not in {VALID_GCP_SERVICES}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "gcp_service": gcp_service})
    gcp_data = _design_gcp_architecture(task, gcp_service, out_type)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_gcp_prompt(state, gcp_data)

    try:
        spec = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    tf_match        = re.search(r'```terraform([\s\S]+?)```', spec)
    terraform_output = tf_match.group(1).strip() if tf_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "gcp_service": gcp_service})

    return {**state, "agent": ROLE, "gcp_spec": spec, "terraform_output": terraform_output, "error": None}
