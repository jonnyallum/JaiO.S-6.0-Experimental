"""
Skill: Quality Validation
Role: quality_validation

Reviews outputs from other skill nodes and validates:
- Completeness (all expected content present?)
- Accuracy (does it match the request?)
- Quality (production-ready?)

Returns a score /10 and PASS/FAIL verdict with specific improvement actions.

Persona injected at runtime via personas/config.py.
"""
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing_extensions import TypedDict

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()
ROLE = "quality_validation"

PASS_THRESHOLD = 7


class QualityValidationState(BaseState):
    artifact: str            # The output to review
    artifact_type: str       # intelligence_report | security_audit | code | plan | copy
    original_query: str      # The original request that produced the artifact
    quality_score: int       # 1-10
    quality_passed: bool     # True if score >= PASS_THRESHOLD
    quality_feedback: str    # Specific feedback and improvement actions


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=45),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _ask_claude(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def quality_validation_node(state: QualityValidationState) -> dict:
    """
    Quality Validation skill node.
    Evaluates an artifact against the original query.
    Scores 1-10, passes at >= 7.
    Returns improvement actions when failing.
    """
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    artifact_type = state.get("artifact_type", "output")
    persona = get_persona(ROLE)

    log.info(f"{ROLE}.started", workflow_id=workflow_id, artifact_type=artifact_type)

    notifier = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    try:
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        prompt = f"""{persona['personality']}

Evaluate this {artifact_type} against the original request. Be strict — we ship nothing below {PASS_THRESHOLD}/10.

━━━ ORIGINAL REQUEST ━━━
{state['original_query']}

━━━ ARTIFACT ({artifact_type}) ━━━
{state['artifact'][:5000]}

━━━ EVALUATION CRITERIA ━━━
Score each 1-10:
1. Completeness — fully addresses the request, no missing sections
2. Accuracy — factually correct and specific
3. Actionability — recommendations are concrete and implementable
4. Clarity — well-structured and free of fluff
5. Production Readiness — deliverable to client without changes

━━━ REQUIRED OUTPUT FORMAT ━━━
## Quality Assessment
**Overall Score:** X/10
**Status:** PASS / FAIL

### Dimension Scores
- Completeness: X/10
- Accuracy: X/10
- Actionability: X/10
- Clarity: X/10
- Production Readiness: X/10

### Issues Found
[Specific problems, or "None"]

### Required Improvements
[Numbered list to reach 9/10. If passing: "None"]

### Verdict
[One sentence]"""

        feedback = _ask_claude(claude, prompt)

        # Parse overall score
        quality_score = 5  # default
        for line in feedback.split("\n"):
            if "Overall Score:" in line:
                try:
                    score_part = line.split("Overall Score:")[1].strip()
                    quality_score = int(score_part.split("/")[0].strip())
                    quality_score = max(1, min(10, quality_score))
                except (ValueError, IndexError):
                    pass
                break

        quality_passed = quality_score >= PASS_THRESHOLD

        if not quality_passed:
            notifier.alert(
                f"⚠️ Quality gate FAILED\n"
                f"Score: {quality_score}/10 | Type: {artifact_type}\n"
                f"Workflow: <code>{workflow_id[:8]}</code>"
            )

        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id=f"{ROLE}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            agent=ROLE,
            state={
                "artifact_type": artifact_type,
                "quality_score": quality_score,
                "quality_passed": quality_passed,
                "status": "completed",
            },
        )

        log.info(f"{ROLE}.completed", score=quality_score, passed=quality_passed)
        return {
            "quality_score": quality_score,
            "quality_passed": quality_passed,
            "quality_feedback": feedback,
            "error": None,
            "workflow_id": workflow_id,
        }

    except Exception as exc:
        msg = f"{ROLE} error: {exc}"
        log.exception(f"{ROLE}.error", error=msg)
        return {
            "quality_score": 0,
            "quality_passed": False,
            "quality_feedback": "",
            "error": msg,
        }


# Backwards-compatibility aliases
qualityguard_node = quality_validation_node
QualityGuardState = QualityValidationState
