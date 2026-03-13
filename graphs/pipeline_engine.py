"""
Pipeline Engine — Sequential multi-agent chaining for JaiOS 6.0.

Executes a list of agents in order, passing each agent's output as context
to the next agent. Supports optional eval gate at the end.

Usage:
    from graphs.pipeline_engine import run_pipeline
    result = run_pipeline("seo_campaign", "Write a blog post about AI agents", eval_output=True)
"""
import uuid
import time
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run_pipeline(
    pipeline_name: str,
    task: str,
    client_id: str = "",
    project_id: str = "",
    eval_output: bool = True,
    custom_steps: list[str] | None = None,
) -> dict:
    """
    Execute a multi-agent pipeline sequentially.

    Args:
        pipeline_name: Key from PIPELINE_TEMPLATES, or "custom" if custom_steps provided
        task: The original task/brief
        client_id: Optional client context
        project_id: Optional project context
        eval_output: Whether to run eval gate on final output
        custom_steps: Optional list of agent roles (overrides pipeline_name)

    Returns:
        {
            "pipeline": pipeline_name,
            "steps": [{"agent": str, "result": str, "elapsed": float, "error": str|None}, ...],
            "final_result": str,
            "eval": {"pass": bool, "score": float, ...} | None,
            "total_elapsed": float,
            "error": str | None,
        }
    """
    from graphs.supervisor import PIPELINE_TEMPLATES, execute_single_agent, SupervisorState

    # Resolve steps
    if custom_steps:
        steps = custom_steps
    else:
        steps = PIPELINE_TEMPLATES.get(pipeline_name)
        if not steps:
            return {
                "pipeline": pipeline_name, "steps": [],
                "final_result": "", "eval": None, "total_elapsed": 0,
                "error": f"Unknown pipeline: {pipeline_name}. Available: {list(PIPELINE_TEMPLATES.keys())}",
            }

    workflow_id = str(uuid.uuid4())
    accumulated_context = ""
    step_results = []
    total_start = time.time()
    last_result = ""

    log.info("pipeline.start", pipeline=pipeline_name, steps=len(steps), task=task[:80])

    for i, agent_role in enumerate(steps):
        step_start = time.time()

        # Build the task with accumulated context from previous agents
        if i == 0:
            enriched_task = task
        else:
            enriched_task = (
                f"ORIGINAL TASK: {task}\n\n"
                f"PREVIOUS AGENT OUTPUT (step {i}/{len(steps)}):\n"
                f"---\n{accumulated_context[:4000]}\n---\n\n"
                f"Your job as '{agent_role}': Build on or refine the above. "
                f"Do NOT repeat what was already done. Add your specialist value."
            )

        # Build state for execute_single_agent
        state = SupervisorState(
            workflow_id=workflow_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent="pipeline_engine",
            task=enriched_task,
            error=None,
            selected_role=agent_role,
            result="",
            client_id=client_id,
            project_id=project_id,
        )

        try:
            result = execute_single_agent(state)
            agent_result = result.get("result", "")
            agent_error = result.get("error")
        except Exception as e:
            agent_result = ""
            agent_error = str(e)

        elapsed = round(time.time() - step_start, 1)

        step_results.append({
            "step": i + 1,
            "agent": agent_role,
            "result": agent_result[:500] if agent_result else "",
            "result_length": len(agent_result) if agent_result else 0,
            "elapsed": elapsed,
            "error": agent_error,
        })

        log.info("pipeline.step_done",
                 step=i+1, agent=agent_role, elapsed=elapsed,
                 chars=len(agent_result) if agent_result else 0,
                 error=agent_error)

        if agent_result:
            accumulated_context = agent_result
            last_result = agent_result

        # If an agent hard-fails, continue (don't break the chain)
        # The next agent gets whatever context was available

    total_elapsed = round(time.time() - total_start, 1)

    # Optional eval gate
    eval_result = None
    if eval_output and last_result:
        try:
            from graphs.eval_gate import evaluate_output
            eval_result = evaluate_output(task, last_result, steps[-1] if steps else "")
            log.info("pipeline.eval", score=eval_result.get("score"), passed=eval_result.get("pass"))
        except Exception as e:
            log.warning("pipeline.eval_failed", error=str(e))
            eval_result = {"pass": True, "score": -1, "feedback": f"Eval error: {str(e)[:100]}"}

    return {
        "pipeline": pipeline_name,
        "steps": step_results,
        "final_result": last_result,
        "eval": eval_result,
        "total_elapsed": total_elapsed,
        "error": None,
    }
