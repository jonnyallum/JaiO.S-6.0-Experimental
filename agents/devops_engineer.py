"""
DevOps Engineer - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), infra_context (str), output_type (VALID_OUTPUT_TYPES), platform (VALID_PLATFORMS)
    Outputs: devops_plan (str), config_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/platform → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before infra analysis
    POST - devops_plan + config_output persisted after successful generation
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

ROLE        = "devops_engineer"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "ci_cd_pipeline", "dockerfile", "env_management", "monitoring_setup",
    "deployment_config", "infra_review", "security_hardening", "general",
}
VALID_PLATFORMS = {
    "vercel", "railway", "fly_io", "aws", "gcp", "hostinger",
    "docker", "github_actions", "general",
}

# ── Platform Profiles ──────────────────────────────────────────────────────────
_PLATFORM_PROFILES = {
    "vercel": {
        "deploy_cmd":    "vercel --prod",
        "env_mgmt":      "vercel env pull .env.local / Vercel dashboard",
        "ci_trigger":    "Git push to main - auto-deploy",
        "strengths":     ["Zero-config Next.js", "edge network", "preview deploys"],
        "gotchas":       ["No persistent disk", "serverless cold starts", "10s function timeout (hobby)"],
        "config_file":   "vercel.json",
    },
    "railway": {
        "deploy_cmd":    "railway up",
        "env_mgmt":      "railway variables set KEY=VALUE",
        "ci_trigger":    "GitHub push or railway up",
        "strengths":     ["Persistent disk", "any language", "built-in Postgres/Redis"],
        "gotchas":       ["Pricing by resource usage", "no edge network"],
        "config_file":   "railway.json / Dockerfile",
    },
    "hostinger": {
        "deploy_cmd":    "rsync -avz --delete dist/ user@host:/path/",
        "env_mgmt":      ".env on server - never commit",
        "ci_trigger":    "GitHub Actions → SSH deploy",
        "strengths":     ["Low cost", "persistent VPS", "full control"],
        "gotchas":       ["Manual SSL renewal if not auto", "no auto-scaling", "SSH key management"],
        "config_file":   "GitHub Actions workflow YAML",
    },
    "github_actions": {
        "deploy_cmd":    "workflow dispatch / push trigger",
        "env_mgmt":      "GitHub Secrets → ${{ secrets.KEY }}",
        "ci_trigger":    "on: push / pull_request / schedule",
        "strengths":     ["Free tier generous", "matrix builds", "native GitHub integration"],
        "gotchas":       ["6h job timeout", "no persistent cache without action", "secrets not in PRs from forks"],
        "config_file":   ".github/workflows/*.yml",
    },
    "general": {
        "deploy_cmd":    "platform-specific",
        "env_mgmt":      "dotenv + secrets manager",
        "ci_trigger":    "git push",
        "strengths":     ["flexible"],
        "gotchas":       ["validate platform match to requirements"],
        "config_file":   "platform-specific",
    },
}

_SECURITY_BASELINE = [
    "Never commit secrets - use platform secret managers",
    "Principle of least privilege on all service accounts",
    "Rotate API keys on every team member departure",
    "Enable dependabot / Renovate for dependency updates",
    "HTTPS only - enforce HSTS headers",
    "Content Security Policy header on all responses",
    "Rate limiting on all public endpoints",
    "Structured logging - no PII in logs",
]


class DevOpsEngineerState(TypedDict, total=False):
    workflow_id:    str
    timestamp:      str
    agent:          str
    error:          str | None
    task:           str
    infra_context:  str
    output_type:    str
    platform:       str
    devops_plan:    str
    config_output:  str


# ── Phase 1 - Infra Analysis (pure, no Claude) ────────────────────────────────
def _analyse_infra(task: str, platform: str) -> dict:
    """Returns infra_data dict - pure lookup and heuristics."""
    profile    = _PLATFORM_PROFILES.get(platform, _PLATFORM_PROFILES["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if "secret" in task_lower or "env" in task_lower:
        flags.append("Environment management required - use platform secrets, never .env in repo")
    if "docker" in task_lower:
        flags.append("Dockerfile needed - use multi-stage build to minimise image size")
    if "monitor" in task_lower or "alert" in task_lower:
        flags.append("Monitoring stack: Sentry (errors) + Uptime Robot (availability) + platform logs")
    if "scale" in task_lower or "load" in task_lower:
        flags.append("Horizontal scaling - ensure stateless app + external session store")
    if "rollback" in task_lower or "zero-downtime" in task_lower:
        flags.append("Zero-downtime deploy: blue-green or rolling strategy required")
    if "cron" in task_lower or "schedule" in task_lower:
        flags.append("Cron jobs: use platform scheduler or GitHub Actions schedule trigger")

    return {**profile, "flags": flags, "security_baseline": _SECURITY_BASELINE}

_build_prompt = None  # assigned below


# ── Phase 2 - Claude DevOps Plan ───────────────────────────────────────────────
def _build_devops_prompt(state: DevOpsEngineerState, infra_data: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    infra_ctx   = state.get("infra_context", "")
    output_type = state.get("output_type", "general")
    platform    = state.get("platform", "general")

    flags_text    = "\n".join(f"  ⚡ {f}" for f in infra_data["flags"]) or "  None detected"
    security_text = "\n".join(f"  • {s}" for s in infra_data["security_baseline"][:5])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Deliver a production-grade {output_type} for {platform}.

PLATFORM PROFILE:
  Deploy:     {infra_data['deploy_cmd']}
  Env Mgmt:   {infra_data['env_mgmt']}
  CI Trigger: {infra_data['ci_trigger']}
  Config:     {infra_data['config_file']}
  Strengths:  {', '.join(infra_data['strengths'])}
  Gotchas:    {', '.join(infra_data['gotchas'])}

INFRA FLAGS:
{flags_text}

SECURITY BASELINE:
{security_text}

TASK:
{task}

CONTEXT:
{infra_ctx or "None provided"}

OUTPUT FORMAT:
## DevOps Plan: {output_type.replace('_', ' ').title()} ({platform})

### Overview
[What this delivers and the deployment strategy]

### Configuration
```yaml
# GitHub Actions / Docker / platform config
```

```bash
# CLI commands in order
```

### Environment Variables
| Variable | Description | Where to Set | Sensitive? |
|---|---|---|---|
[rows]

### Security Hardening
[Specific measures for this deployment]

### Monitoring & Alerts
[What to monitor, which tools, alert thresholds]

### Rollback Procedure
[Step-by-step rollback - tested, not theoretical]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_devops_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    metrics.log()
    metrics.persist()
    return response.content[0].text


def devops_engineer_node(state: DevOpsEngineerState) -> DevOpsEngineerState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    output_type = state.get("output_type", "general")
    platform    = state.get("platform", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"PERMANENT: platform '{platform}' not in {VALID_PLATFORMS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": output_type, "platform": platform})

    infra_data = _analyse_infra(task, platform)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_devops_prompt(state, infra_data)

    try:
        plan = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    config_match  = re.search(r'```(?:yaml|bash)([\s\S]+?)```', plan)
    config_output = config_match.group(1).strip() if config_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": output_type, "platform": platform})

    return {
        **state,
        "agent":         ROLE,
        "devops_plan":   plan,
        "config_output": config_output,
        "error":         None,
    }
