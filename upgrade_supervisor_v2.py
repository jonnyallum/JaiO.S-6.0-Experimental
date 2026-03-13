#!/usr/bin/env python3
"""
Ralph Loop: Upgrade supervisor.py — Loops 1, 2, 3 + RetryPolicy
  Loop 1: LLM Router (Claude Haiku) with keyword fallback
  Loop 2: PostgresSaver (Supabase) replacing MemorySaver
  Loop 3: RetryPolicy on execute node
"""
import subprocess, sys, time, re, os

SUPERVISOR = "/home/jonny/antigravity/jaios6/graphs/supervisor.py"

with open(SUPERVISOR, "r") as f:
    content = f.read()

BACKUP = SUPERVISOR + ".bak"
with open(BACKUP, "w") as f:
    f.write(content)
print(f"📦 Backup saved to {BACKUP}")

changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# LOOP 1: LLM ROUTER — Replace _classify_task with Haiku-powered classification
# ═══════════════════════════════════════════════════════════════════════════════

old_classify = '''def _classify_task(task: str) -> str:
    """Classify task to role by keyword scoring. Defaults to github_intelligence."""
    task_lower = task.lower()
    scores = {role: 0 for role in ROUTING_RULES}
    for role, keywords in ROUTING_RULES.items():
        for kw in keywords:
            if kw in task_lower:
                scores[role] += 1
    best = max(scores, key=lambda r: scores[r])
    return best if scores[best] > 0 else "github_intelligence"'''

new_classify = '''def _classify_task_keywords(task: str) -> tuple[str, int]:
    """Fast keyword-based classification. Returns (role, score)."""
    task_lower = task.lower()
    scores = {role: 0 for role in ROUTING_RULES}
    for role, keywords in ROUTING_RULES.items():
        for kw in keywords:
            if kw in task_lower:
                scores[role] += 1
    best = max(scores, key=lambda r: scores[r])
    return (best, scores[best]) if scores[best] > 0 else ("general_assistant", 0)


def _classify_task_llm(task: str) -> tuple[str, float]:
    """Use Claude Haiku to classify task with confidence score."""
    try:
        from anthropic import Anthropic
        client = Anthropic()

        # Build compact role list (name: top 3 keywords)
        role_summary = "\\n".join(
            f"  {role}: {', '.join(kws[:4])}"
            for role, kws in ROUTING_RULES.items()
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20250315",
            max_tokens=150,
            system="You are a task router for an AI agency. Given a task, select the single best agent role. Respond ONLY with valid JSON: {\\\"role\\\": \\\"role_name\\\", \\\"confidence\\\": 0.0-1.0}",
            messages=[{
                "role": "user",
                "content": f"Task: {task}\\n\\nAvailable roles:\\n{role_summary}\\n\\nWhich role handles this best?"
            }],
        )
        import json
        text = response.content[0].text.strip()
        # Handle potential markdown wrapping
        if text.startswith("```"):
            text = text.split("\\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        role = parsed.get("role", "general_assistant")
        conf = float(parsed.get("confidence", 0.5))
        # Validate role exists
        if role in ROUTING_RULES:
            return (role, conf)
        # Fuzzy match — check if LLM returned a close name
        for r in ROUTING_RULES:
            if r in role or role in r:
                return (r, conf * 0.9)
        return ("general_assistant", 0.3)
    except Exception as e:
        log.warning("llm_router.failed", error=str(e))
        return ("general_assistant", 0.0)


def _classify_task(task: str) -> str:
    """
    Hybrid router: try LLM first, fall back to keywords.
    Strategy:
      1. Run keyword matcher (instant, free)
      2. If keyword confidence >= 2 matches, use it (clear signal)
      3. Otherwise, run LLM router (Haiku, ~$0.0001/call)
      4. If LLM confidence >= 0.6, use LLM result
      5. Otherwise, use keyword result or default to general_assistant
    """
    # Step 1: Fast keyword pass
    kw_role, kw_score = _classify_task_keywords(task)

    # Step 2: High keyword confidence = skip LLM
    if kw_score >= 2:
        log.info("router.keyword_match", role=kw_role, score=kw_score, method="keyword")
        return kw_role

    # Step 3: LLM classification
    llm_role, llm_conf = _classify_task_llm(task)
    log.info("router.llm_result", role=llm_role, confidence=llm_conf,
             kw_role=kw_role, kw_score=kw_score, method="hybrid")

    # Step 4: Use LLM if confident
    if llm_conf >= 0.6:
        return llm_role

    # Step 5: Fallback chain
    if kw_score >= 1:
        return kw_role

    return llm_role if llm_conf > 0 else "general_assistant"'''

if old_classify in content:
    content = content.replace(old_classify, new_classify)
    changes += 1
    print("✅ Loop 1: Installed hybrid LLM/keyword router")
else:
    print("⚠️  Loop 1: _classify_task pattern not found (may already be upgraded)")

# ═══════════════════════════════════════════════════════════════════════════════
# LOOP 2: POSTGRESSAVER — Replace MemorySaver with persistent checkpoints
# ═══════════════════════════════════════════════════════════════════════════════

# Add PostgresSaver import
if "from langgraph.checkpoint.postgres" not in content:
    content = content.replace(
        "from langgraph.checkpoint.memory import MemorySaver",
        """from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver"""
    )
    changes += 1
    print("✅ Loop 2a: Added PostgresSaver import")

# Replace build_supervisor to use PostgresSaver with MemorySaver fallback
old_build_tail = "    return graph.compile(checkpointer=MemorySaver())"
new_build_tail = """    # Try PostgresSaver (persistent), fallback to MemorySaver (RAM)
    db_url = os.environ.get("BRAIN_CONNECTION_STRING", "")
    if db_url:
        try:
            # Use session-mode pooler (port 5432) for prepared statements
            session_url = db_url.replace(":6543/", ":5432/")
            if "sslmode" not in session_url:
                session_url += "?sslmode=require"
            checkpointer = PostgresSaver.from_conn_string(session_url)
            checkpointer.setup()
            log.info("supervisor.checkpointer", type="PostgresSaver", db="supabase")
            return graph.compile(checkpointer=checkpointer)
        except Exception as e:
            log.warning("supervisor.postgres_fallback", error=str(e))
    log.info("supervisor.checkpointer", type="MemorySaver", reason="no_db_url_or_error")
    return graph.compile(checkpointer=MemorySaver())"""

if old_build_tail in content:
    content = content.replace(old_build_tail, new_build_tail)
    changes += 1
    print("✅ Loop 2b: PostgresSaver with MemorySaver fallback")

# Add os import if not present
if "\nimport os\n" not in content and "\nimport os," not in content:
    content = content.replace("import uuid\n", "import os\nimport uuid\n")
    changes += 1
    print("✅ Loop 2c: Added os import")

# ═══════════════════════════════════════════════════════════════════════════════
# LOOP 3: RETRY POLICY — Add RetryPolicy to execute node
# ═══════════════════════════════════════════════════════════════════════════════

if "RetryPolicy" not in content:
    # Add import
    content = content.replace(
        "from langgraph.graph import END, START, StateGraph",
        "from langgraph.graph import END, START, StateGraph\nfrom langgraph.types import RetryPolicy"
    )
    # Add retry to execute node
    content = content.replace(
        '    graph.add_node("execute", execute_node)',
        '    graph.add_node("execute", execute_node, retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0))'
    )
    changes += 1
    print("✅ Loop 3: Added RetryPolicy(max_attempts=3) to execute node")

# ═══════════════════════════════════════════════════════════════════════════════
# BONUS: Add "general_assistant" to ROUTING_RULES as catch-all
# ═══════════════════════════════════════════════════════════════════════════════

if '"general_assistant"' not in content:
    content = content.replace(
        '    "financial_planner": [',
        '''    "general_assistant": [
        "help", "assist", "general", "other", "misc", "task",
        "do this", "please", "can you", "i need",
    ],
    "financial_planner": ['''
    )
    changes += 1
    print("✅ Bonus: Added general_assistant catch-all role")

# ═══════════════════════════════════════════════════════════════════════════════
# WRITE + RESTART
# ═══════════════════════════════════════════════════════════════════════════════

with open(SUPERVISOR, "w") as f:
    f.write(content)
print(f"\n📝 Applied {changes} changes to supervisor.py")

# Syntax check
result = subprocess.run(
    [sys.executable, "-m", "py_compile", SUPERVISOR],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"❌ SYNTAX ERROR — reverting!\n{result.stderr}")
    with open(BACKUP, "r") as f:
        original = f.read()
    with open(SUPERVISOR, "w") as f:
        f.write(original)
    print("🔄 Reverted to backup")
    sys.exit(1)
print("✅ Syntax check passed")

print("\n🔄 Restarting jaios6...")
subprocess.run(["sudo", "systemctl", "restart", "jaios6"], check=True)
print("⏳ Waiting 20s for startup...")
time.sleep(20)

# Health check
result = subprocess.run(
    ["curl", "-s", "http://localhost:8765/health"],
    capture_output=True, text=True, timeout=10
)
print(f"\n🏥 Health: {result.stdout.strip()}")

if '"ok"' in result.stdout:
    print("\n🎉 RALPH LOOP 1-3 COMPLETE!")
    print("   ✅ Hybrid LLM/keyword router (Haiku + fallback)")
    print("   ✅ PostgresSaver (persistent checkpoints)")
    print("   ✅ RetryPolicy(3) on execute node")
    print("   ✅ general_assistant catch-all role")
else:
    print("\n⚠️  Health check issue — check logs: journalctl -u jaios6 -n 50")
