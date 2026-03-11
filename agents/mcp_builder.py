"""
MCP Server Builder — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), mcp_context (str), output_type (VALID_OUTPUT_TYPES), transport (VALID_TRANSPORTS)
    Outputs: mcp_spec (str), server_code (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/transport → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before MCP spec generation
    POST — mcp_spec + server_code persisted after successful generation
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

ROLE        = "mcp_builder"
MAX_RETRIES = 3
MAX_TOKENS  = 2800

VALID_OUTPUT_TYPES = {
    "server_spec", "tool_definition", "resource_definition", "prompt_template",
    "full_server", "debug_report", "integration_guide", "general",
}
VALID_TRANSPORTS = {"stdio", "sse", "http", "general"}

# ── MCP Knowledge Base ─────────────────────────────────────────────────────────
_MCP_BOILERPLATE = {
    "stdio": """import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const server = new Server(
  { name: "mcp-server-name", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [/* tool definitions */],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  // dispatch by tool name
  return { content: [{ type: "text", text: "result" }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);""",
    "sse": """import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
// SSE transport — use with Express or Hono for HTTP server""",
    "general": "// Transport TBD based on deployment context",
}

_TOOL_TEMPLATE = """{
  name: "tool_name",
  description: "Clear one-line description of what this tool does",
  inputSchema: {
    type: "object",
    properties: {
      param1: { type: "string", description: "Description of param1" },
    },
    required: ["param1"],
  },
}"""

_MCP_GOTCHAS = [
    "Tool names must be snake_case — no hyphens",
    "inputSchema must be valid JSON Schema — test with ajv",
    "Error responses must use McpError, not thrown JS errors",
    "stdio transport: all debug output to stderr, never stdout",
    "dotenv: path from dist/ to root = '../../../.env' (3 levels if src/index.ts → dist/index.js)",
    "Always test with: echo '{}' | node dist/index.js (stdio) or curl for SSE",
    "Return type must match: { content: [{ type: 'text', text: string }] }",
    "Resource URIs must be unique and stable across calls",
]

_MCP_CHECKLIST = [
    "Server name and version set in constructor",
    "Capabilities declared: tools / resources / prompts",
    "ListTools handler returns all tool definitions",
    "CallTool handler dispatches correctly by tool name",
    "All inputs validated before use",
    "Errors returned as McpError not thrown",
    "Transport connected as last step",
    "dotenv loaded at top if env vars needed",
    "Startup message written to stderr for debug",
]


class McpBuilderState(TypedDict, total=False):
    workflow_id:  str
    timestamp:    str
    agent:        str
    error:        str | None
    task:         str
    mcp_context:  str
    output_type:  str
    transport:    str
    mcp_spec:     str
    server_code:  str


# ── Phase 1 — MCP Design (pure, no Claude) ────────────────────────────────────
def _design_mcp_structure(task: str, transport: str, output_type: str) -> dict:
    """Returns mcp_data dict — pure lookup, no Claude."""
    boilerplate = _MCP_BOILERPLATE.get(transport, _MCP_BOILERPLATE["general"])
    task_lower  = task.lower()
    flags: list[str] = []

    if "resource" in task_lower:
        flags.append("Resources required — define stable URI scheme: protocol://path/{id}")
    if "prompt" in task_lower:
        flags.append("Prompt templates required — use ListPromptsRequestSchema")
    if "auth" in task_lower or "token" in task_lower:
        flags.append("Auth tokens: load via dotenv / process.env — never hardcode")
    if "scrape" in task_lower or "fetch" in task_lower:
        flags.append("HTTP fetching: use node-fetch or built-in fetch — handle rate limits")
    if "database" in task_lower or "supabase" in task_lower:
        flags.append("DB connection: initialise once at server startup, not per-request")

    return {
        "boilerplate":  boilerplate,
        "tool_template": _TOOL_TEMPLATE,
        "gotchas":      _MCP_GOTCHAS,
        "checklist":    _MCP_CHECKLIST,
        "flags":        flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude MCP Spec ──────────────────────────────────────────────────
def _build_mcp_prompt(state: McpBuilderState, mcp_data: dict) -> str:
    persona    = get_persona(ROLE)
    task       = state["task"]
    ctx        = state.get("mcp_context", "")
    out_type   = state.get("output_type", "full_server")
    transport  = state.get("transport", "stdio")

    flags_text    = "
".join(f"  ⚡ {f}" for f in mcp_data["flags"]) or "  None detected"
    gotchas_text  = "
".join(f"  ⚠ {g}" for g in mcp_data["gotchas"])
    checklist_txt = "
".join(f"  ☐ {c}" for c in mcp_data["checklist"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Build a production-ready MCP server — output type: {out_type}, transport: {transport}.

BOILERPLATE:
```typescript
{mcp_data['boilerplate']}
```

TOOL TEMPLATE:
```typescript
{mcp_data['tool_template']}
```

DESIGN FLAGS:
{flags_text}

CRITICAL GOTCHAS:
{gotchas_text}

BUILD CHECKLIST:
{checklist_txt}

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## MCP Server: {out_type.replace('_',' ').title()} ({transport})

### Server Overview
[Name, version, capabilities, purpose — 3 sentences]

### Tool Definitions
```typescript
// Complete tool definitions with inputSchema
```

### Server Implementation
```typescript
// Full server code — production ready
```

### .mcp.json Registration
```json
{{
  "mcpServers": {{
    "server-name": {{
      "command": "node",
      "args": ["path/to/dist/index.js"],
      "env": {{}}
    }}
  }}
}}
```

### Checklist Sign-off
[Each item: PASS / ADDRESSED / N/A]

### Testing Instructions
```bash
# How to test each tool
```

### Next Action
[Single most important first step]
"""

_build_prompt = _build_mcp_prompt  # spec alias


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


def mcp_builder_node(state: McpBuilderState) -> McpBuilderState:
    thread_id  = state.get("workflow_id", "local")
    task       = state.get("task", "").strip()
    out_type   = state.get("output_type", "full_server")
    transport  = state.get("transport", "stdio")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"PERMANENT: transport '{transport}' not in {VALID_TRANSPORTS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "transport": transport})
    mcp_data = _design_mcp_structure(task, transport, out_type)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_mcp_prompt(state, mcp_data)

    try:
        spec = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    code_match  = re.search(r'```typescript([\s\S]+?)```', spec)
    server_code = code_match.group(1).strip() if code_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "transport": transport})

    return {**state, "agent": ROLE, "mcp_spec": spec, "server_code": server_code, "error": None}
