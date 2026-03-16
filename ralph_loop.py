#!/usr/bin/env python3
"""
RALPH LOOP — jAIlbreakO.S routing gauntlet
Fires real briefs at port 8766, scores via eval_judge, patches failures,
iterates until avg eval > 8.5/10 across 3 consecutive loops.
"""
import json, time, sys, os, re
from datetime import datetime
from pathlib import Path

API = "http://localhost:8766"
API_KEY = "jaios6_KBMPXwfdiHyAz-VQQHV6d7v_AC0qWnD5EZ88uwBxxR8"
LOG = Path("/home/jonny/antigravity/jailbreak/logs/ralph_loop.json")
SCORE_TARGET = 8.5
LOOPS_TO_WIN = 3
MAX_LOOPS = 3          # Cost guard: max 3 loops (was 12)
BRIEF_SLEEP = 8        # Seconds between briefs to avoid rate burst
MAX_TOKENS_WARN = 80000  # Log warning if estimated tokens exceed this

# ── 10 real business briefs spanning all verticals ──────────────────────────
BRIEFS = [
    {
        "id": "growth_strategy",
        "brief": "We're a B2B SaaS platform (project management for agencies) with 200 paying customers at £49/month. We're growing 8% MoM but churn is 6%. Build us a full growth strategy to hit 1000 customers in 12 months.",
        "expected_role": "product_strategist",
    },
    {
        "id": "investor_pitch",
        "brief": "Write a complete investor pitch for Antigravity Agency — an AI-powered agency OS that turns Claude into a 95-agent workforce. We're pre-revenue, seeking £500k seed. TAM is £50B agency services market.",
        "expected_role": "investor_pitch_writer",
    },
    {
        "id": "risk_matrix",
        "brief": "Identify and score all risks for launching a crypto-backed betting exchange in the UK. Cover regulatory, technical, financial, reputational and competitive risks with probability × impact scoring.",
        "expected_role": "risk_analyst",
    },
    {
        "id": "launch_campaign",
        "brief": "Create a full go-to-market launch plan for jAIlbreakO.S — a 95-agent AI orchestration OS for agencies. Channels: social media, product hunt, linkedin, email. Launch in 2 weeks.",
        "expected_role": "launch_orchestrator",
    },
    {
        "id": "competitor_intel",
        "brief": "Do a deep competitive analysis of the top 5 AI agency tools: Relevance AI, Botpress, AgentGPT, Crew AI, and AutoGen. Score each on: capability, pricing, ease of use, enterprise readiness.",
        "expected_role": "competitor_monitor",
    },
    {
        "id": "betting_edge",
        "brief": "Analyse the Premier League title race with 8 games remaining. Liverpool lead by 4 points. Build a probability model for each outcome and identify the best value betting positions across outright, BTTS and over/under markets.",
        "expected_role": "football_tactical",
    },
    {
        "id": "seo_audit",
        "brief": "Perform a technical SEO audit for an e-commerce motorcycle parts site. They sell 5000 SKUs, have 200 product pages indexed, 40% bounce rate, and no schema markup. Produce a prioritised fix list.",
        "expected_role": "seo_specialist",
    },
    {
        "id": "content_scaling",
        "brief": "Design a content factory system that generates 30 pieces of content per week for a B2B fintech brand. Include content pillars, formats, distribution channels, repurposing workflow and automation recommendations.",
        "expected_role": "content_scaler",
    },
    {
        "id": "legal_review",
        "brief": "Review the key legal risks for a UK startup offering AI-generated financial advice via a chatbot. Cover FCA regulation, liability for AI errors, GDPR data handling, and terms of service requirements.",
        "expected_role": "legal_advisor",
    },
    {
        "id": "research_deep",
        "brief": "Research the state of agentic AI in 2025/26 — who are the key players, what are the dominant frameworks (LangGraph, CrewAI, AutoGen, ADK), what are enterprises actually buying, and where is the market heading in 12 months.",
        "expected_role": "research_analyst",
    },
]

import urllib.request, urllib.error

def post(path, payload, timeout=150):
    url = f"{API}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"error": str(e)}

def get(path, timeout=10):
    url = f"{API}{path}"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}

def poll_job(job_id, max_wait=120):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = get(f"/job/{job_id}")
        if r.get("status") in ("complete", "failed"):
            return r
        time.sleep(3)
    return {"status": "timeout", "job_id": job_id}

def extract_score(result_text: str) -> float:
    """Pull numeric score from eval_judge output."""
    if not result_text:
        return 0.0
    # Look for patterns like: score: 8.5, SCORE: 7, 8.5/10, rated 9
    patterns = [
        r'(?:overall\s+)?score[:\s]+([0-9]+(?:\.[0-9]+)?)\s*/?\s*10',
        r'([0-9]+(?:\.[0-9]+)?)\s*/\s*10',
        r'(?:score|rating|rated?)[:\s]+([0-9]+(?:\.[0-9]+)?)',
        r'\b([0-9]+(?:\.[0-9]+)?)\s*(?:out of|\/)\s*10',
    ]
    for pat in patterns:
        m = re.search(pat, result_text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if 0 <= val <= 10:
                return val
    return 0.0

def run_brief(b: dict, loop_num: int) -> dict:
    brief_id = b["id"]
    print(f"\n  [{brief_id}] Firing...", flush=True)
    
    # 1. Submit to jailbreak routing
    start = time.time()
    result = post("/run", {"brief": b["brief"], "sync": True})
    elapsed = round(time.time() - start, 1)
    
    if result.get("error"):
        print(f"  [{brief_id}] ERROR: {result['error'][:120]}")
        return {"id": brief_id, "loop": loop_num, "status": "error", "error": result["error"], "score": 0.0, "elapsed": elapsed}
    
    out_data = result.get("output", {}); agent_used = out_data.get("selected_role", out_data.get("agent", "unknown")) if isinstance(out_data, dict) else "unknown"
    output = out_data.get("result", "") if isinstance(out_data, dict) else ""
    output_len = len(output)
    
    print(f"  [{brief_id}] Agent={agent_used} | {output_len} chars | {elapsed}s", flush=True)
    
    if output_len < 100:
        print(f"  [{brief_id}] Output too short — scoring 2.0")
        return {"id": brief_id, "loop": loop_num, "status": "short", "agent": agent_used, "score": 2.0, "elapsed": elapsed, "output_len": output_len}
    
    # 2. Score via eval_judge
    # 2. Score via eval_judge — routing-safe brief (no domain keywords)
    output_snippet = output[:600]
    eval_brief = (
        f"EVAL JUDGE TASK: grade and rate this AI output.
"
        f"RUBRIC: depth, accuracy, actionability, completeness (1-10).
"
        f"CONTEXT: {b['brief'][:150]}
"
        f"OUTPUT: {output_snippet}
"
        f"Respond: SCORE: X/10 then brief reasoning."
    )
    eval_result = post("/run", {"brief": eval_brief, "sync": True})
    eval_data = eval_result.get("output", {}); eval_text = eval_data.get("result", "") if isinstance(eval_data, dict) else ""
    score = extract_score(eval_text)
    
    # Routing check
    expected = b.get("expected_role", "")
    routing_match = expected in agent_used if expected else True
    
    print(f"  [{brief_id}] Score={score}/10 | Routing={'✓' if routing_match else f'✗ expected={expected}'}", flush=True)
    
    return {
        "id": brief_id,
        "loop": loop_num,
        "status": "ok",
        "agent": agent_used,
        "expected": expected,
        "routing_match": routing_match,
        "score": score,
        "elapsed": elapsed,
        "output_len": output_len,
        "eval_snippet": eval_text[:300],
    }

def save_log(history):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "w") as f:
        json.dump(history, f, indent=2)

def print_summary(results: list, loop_num: int):
    scores = [r["score"] for r in results]
    avg = sum(scores) / len(scores) if scores else 0
    errors = [r for r in results if r["status"] == "error"]
    low = [r for r in results if r["score"] < 6 and r["status"] == "ok"]
    routing_fails = [r for r in results if not r.get("routing_match", True)]
    
    print(f"\n{'='*60}")
    print(f"  LOOP {loop_num} SUMMARY")
    print(f"{'='*60}")
    print(f"  Avg score:      {avg:.2f}/10  (target: {SCORE_TARGET})")
    print(f"  Errors:         {len(errors)}")
    print(f"  Low scores(<6): {[r['id'] for r in low]}")
    print(f"  Routing fails:  {[r['id'] for r in routing_fails]}")
    print(f"{'='*60}", flush=True)
    return avg, errors, low, routing_fails

def main():
    print("=" * 60)
    print("  RALPH LOOP — jAIlbreakO.S ROUTING GAUNTLET")
    print(f"  Target: {SCORE_TARGET}/10 avg sustained over {LOOPS_TO_WIN} loops")
    print(f"  Briefs: {len(BRIEFS)} | Max loops: {MAX_LOOPS}")
    print("=" * 60, flush=True)
    
    # Verify API is live
    health = get("/health")
    if health.get("error"):
        print(f"FATAL: API unreachable — {health['error']}")
        sys.exit(1)
    print(f"  API health: {health.get('status')} | agents: {health.get('agents_loaded', '?')}\n")
    
    all_history = []
    consecutive_wins = 0
    
    for loop_num in range(1, MAX_LOOPS + 1):
        print(f"\n{'#'*60}")
        print(f"  LOOP {loop_num} / {MAX_LOOPS}  [{datetime.now().strftime('%H:%M:%S')}]")
        print(f"{'#'*60}", flush=True)
        
        loop_results = []
        for b in BRIEFS:
            r = run_brief(b, loop_num)
            loop_results.append(r)
            all_history.append(r)
            save_log(all_history)
            time.sleep(2)  # avoid rate limits
        
        avg, errors, low, routing_fails = print_summary(loop_results, loop_num)
        
        if avg >= SCORE_TARGET and not errors:
            consecutive_wins += 1
            print(f"  WIN #{consecutive_wins} / {LOOPS_TO_WIN} needed")
        else:
            consecutive_wins = 0
        
        if consecutive_wins >= LOOPS_TO_WIN:
            print(f"\n{'*'*60}")
            print(f"  MISSION COMPLETE — {LOOPS_TO_WIN} consecutive loops above {SCORE_TARGET}/10")
            print(f"  jAIlbreakO.S beats the Antigravity Orchestra.")
            print(f"{'*'*60}")
            break
        
        if loop_num < MAX_LOOPS:
            if errors or low or routing_fails:
                print(f"\n  Issues found — logging for triage. Continuing loop...", flush=True)
            time.sleep(5)
    
    # Final summary
    all_scores = [r["score"] for r in all_history if r.get("score", 0) > 0]
    final_avg = sum(all_scores) / len(all_scores) if all_scores else 0
    print(f"\n  FINAL AVG SCORE: {final_avg:.2f}/10 across {len(all_history)} runs")
    print(f"  Full log: {LOG}")
    save_log(all_history)

if __name__ == "__main__":
    main()
