"""
Eval Gate — LLM-as-judge quality gate for pipeline outputs.
Scores output 1-10 across relevance, completeness, clarity.
If score < threshold, returns improvement feedback.
"""
import os
import logging
import json
import re

import anthropic

log = logging.getLogger(__name__)

EVAL_MODEL = "claude-sonnet-4-20250514"
PASS_THRESHOLD = 6  # Minimum average score to pass


def evaluate_output(task: str, output: str, agent_role: str = "") -> dict:
    """
    Judge an agent's output against the original task.
    Returns {
        "pass": bool,
        "score": float (1-10 average),
        "scores": {"relevance": int, "completeness": int, "clarity": int},
        "feedback": str,
    }
    """
    if not output or len(output.strip()) < 20:
        return {
            "pass": False, "score": 0,
            "scores": {"relevance": 0, "completeness": 0, "clarity": 0},
            "feedback": "Output is empty or trivially short.",
        }

    client = anthropic.Anthropic()
    prompt = f"""You are a strict quality judge for an AI agency.

ORIGINAL TASK: {task}
AGENT ROLE: {agent_role or "unknown"}
AGENT OUTPUT:
---
{output[:3000]}
---

Score this output on three dimensions (1-10 each):
1. RELEVANCE: Does it actually address the task? (1=completely off-topic, 10=perfectly on-target)
2. COMPLETENESS: Is the response thorough? (1=barely started, 10=comprehensive)
3. CLARITY: Is it well-structured and actionable? (1=incoherent, 10=crystal clear)

Respond ONLY with valid JSON:
{{"relevance": N, "completeness": N, "clarity": N, "feedback": "one sentence"}}"""

    try:
        resp = client.messages.create(
            model=EVAL_MODEL, max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Extract JSON from response
        match = re.search(r'\{[^}]+\}', text)
        if match:
            data = json.loads(match.group())
        else:
            data = json.loads(text)

        scores = {
            "relevance": int(data.get("relevance", 5)),
            "completeness": int(data.get("completeness", 5)),
            "clarity": int(data.get("clarity", 5)),
        }
        avg = sum(scores.values()) / 3
        return {
            "pass": avg >= PASS_THRESHOLD,
            "score": round(avg, 1),
            "scores": scores,
            "feedback": data.get("feedback", ""),
        }
    except Exception as e:
        log.warning("eval_gate.error", error=str(e))
        # On eval failure, pass through (don't block pipeline)
        return {
            "pass": True, "score": -1,
            "scores": {"relevance": -1, "completeness": -1, "clarity": -1},
            "feedback": f"Eval failed: {str(e)[:100]}",
        }
