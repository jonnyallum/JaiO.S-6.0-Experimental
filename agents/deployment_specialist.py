"""
Deployment Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), deploy_context (str), output_type (VALID_OUTPUT_TYPES), target (VALID_TARGETS)
    Outputs: deployment_plan (str), deploy_commands (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/target → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before deployment pre-flight checks
    POST - deployment_plan + deploy_commands persisted after successful generation
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

ROLE        = "deployment_specialist"
MAX_RETRIES = 3
MAX_TOKENS  = 2200

VALID_OUTPUT_TYPES = {
    "deployment_runbook", "rollback_plan", "pre_flight_checklist",
    "zero_downtime_strategy", "hotfix_procedure", "release_notes", "general",
}
VALID_TARGETS = {
    "vercel", "hostinger_vps", "railway", "fly_io",
    "github_pages", "docker_compose", "general",
}

# ── Pre-flight Gate Registry ───────────────────────────────────────────────────
_PREFLIGHT_GATES = {
    "vercel": [
        "✓ All env vars set in Vercel dashboard (not just .env.local)",
        "✓ Build passes locally: npm run build",
        "✓ No console.error / unhandled promise rejections in build output",
        "✓ Preview deploy reviewed and approved",
        "✓ Sentry / error tracking configured for production",
        "✓ Analytics event verified on preview",
        "✓ Redirect rules in vercel.json tested",
        "✓ Domain / SSL certificate active",
    ],
    "hostinger_vps": [
        "✓ SSH key authentication - password auth disabled",
        "✓ Firewall: only 22, 80, 443 open",
        "✓ Nginx config tested: nginx -t",
        "✓ SSL cert valid: certbot renew --dry-run",
        "✓ Backup taken before deploy",
        "✓ PM2 / systemd process manager configured",
        "✓ Log rotation configured",
        "✓ Rollback tarball ready at /var/backups/",
    ],
    "railway": [
        "✓ Environment variables set in Railway dashboard",
        "✓ Health check endpoint returning 200",
        "✓ Database migrations run successfully",
        "✓ Volume mounts correct for persistent data",
        "✓ Service dependencies (DB, Redis) healthy before app deploy",
    ],
    "general": [
        "✓ Build passes in CI",
        "✓ Secrets confirmed in target environment",
        "✓ Database migrations tested on staging",
        "✓ Rollback procedure documented and tested",
        "✓ Monitoring alerts active",
    ],
}

_ROLLBACK_STRATEGIES = {
    "vercel":        "vercel rollback [deployment-url] - instant, no downtime",
    "hostinger_vps": "Restore from /var/backups/ tarball + restart PM2",
    "railway":       "Railway dashboard → Deployments → Rollback to previous",
    "fly_io":        "fly deploy --image [previous-image-tag]",
    "general":       "Revert git commit + redeploy OR restore from snapshot",
}

_DOWNTIME_RISK_SIGNALS = {
    "database migration":  "HIGH - test on staging first, use transactions",
    "dependency upgrade":  "MEDIUM - breaking changes possible",
    "env var change":      "LOW - immediate effect, no restart needed on most platforms",
    "new feature":         "LOW - behind feature flag recommended",
    "infrastructure":      "HIGH - blue-green or canary deploy required",
}


class DeploymentSpecialistState(TypedDict, total=False):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    task:             str
    deploy_context:   str
    output_type:      str
    target:           str
    deployment_plan:  str
    deploy_commands:  str


# ── Phase 1 - Pre-flight Analysis (pure, no Claude) ───────────────────────────
def _run_preflight_analysis(task: str, target: str) -> dict:
    """Returns preflight_data dict - pure lookup and risk scoring."""
    gates       = _PREFLIGHT_GATES.get(target, _PREFLIGHT_GATES["general"])
    rollback    = _ROLLBACK_STRATEGIES.get(target, _ROLLBACK_STRATEGIES["general"])
    task_lower  = task.lower()

    risk_level  = "LOW"
    risk_flags: list[str] = []
    for signal, risk in _DOWNTIME_RISK_SIGNALS.items():
        if signal in task_lower:
            risk_flags.append(f"{signal} → {risk}")
            if "HIGH" in risk:
                risk_level = "HIGH"
            elif "MEDIUM" in risk and risk_level != "HIGH":
                risk_level = "MEDIUM"

    return {
        "gates":      gates,
        "rollback":   rollback,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Deployment Plan ──────────────────────────────────────────
def _build_deploy_prompt(state: DeploymentSpecialistState, preflight: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    ctx         = state.get("deploy_context", "")
    output_type = state.get("output_type", "deployment_runbook")
    target      = state.get("target", "general")

    gates_text  = "\n".join(f"  {g}" for g in preflight["gates"])
    risks_text  = "\n".join(f"  ⚠ {r}" for r in preflight["risk_flags"]) or "  No high-risk signals detected"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Produce a zero-downtime {output_type} for deploying to {target}.

RISK ASSESSMENT: {preflight['risk_level']}
{risks_text}

ROLLBACK STRATEGY: {preflight['rollback']}

PRE-FLIGHT GATES:
{gates_text}

TASK:
{task}

DEPLOY CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Deployment Plan: {output_type.replace('_', ' ').title()} → {target}

### Risk Level: {preflight['risk_level']}
[2-sentence justification]

### Pre-flight Checklist
[Go through each gate - verify or add target-specific gates]

### Deployment Steps
```bash
# Ordered commands - each with a comment explaining why
```

### Zero-Downtime Measures
[Specific technique for this stack - blue-green / rolling / feature flags]

### Verification Steps (post-deploy)
[What to check, in order, within 5 minutes of deploying]

### Rollback Procedure
```bash
# Exact commands to roll back - tested, not theoretical
```

### Communication
[Who to notify, when, and what to say - internal + external if customer-facing]

### Next Action
[Single most important step right now]
"""

_build_prompt = _build_deploy_prompt  # spec alias


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


def deployment_specialist_node(state: DeploymentSpecialistState) -> DeploymentSpecialistState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    output_type = state.get("output_type", "deployment_runbook")
    target      = state.get("target", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")
    if target not in VALID_TARGETS:
        raise ValueError(f"PERMANENT: target '{target}' not in {VALID_TARGETS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": output_type, "target": target})

    preflight = _run_preflight_analysis(task, target)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_deploy_prompt(state, preflight)

    try:
        plan = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    cmd_match       = re.search(r'```bash([\s\S]+?)```', plan)
    deploy_commands = cmd_match.group(1).strip() if cmd_match else ""

    checkpoint("POST", thread_id, ROLE, {
        "target": target, "risk_level": preflight["risk_level"],
    })

    return {
        **state,
        "agent":            ROLE,
        "deployment_plan":  plan,
        "deploy_commands":  deploy_commands,
        "error":            None,
    }
