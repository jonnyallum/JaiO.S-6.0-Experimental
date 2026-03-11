"""
JaiOS 6.0 — Live Orchestra Test
Direct agent imports bypassing __init__.py (which requires full settings stack).
Run: .venv/bin/python test_orchestra.py
"""
import sys, os, uuid, time
sys.path.insert(0, "/home/jonny/antigravity/jaios6")

from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

import importlib.util

RESULTS = []
BASE = "/home/jonny/antigravity/jaios6/agents"

def base(role):
    return {
        "workflow_id": str(uuid.uuid4()),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "agent":       role,
        "error":       None,
    }

def load_agent(fname, node_name):
    spec = importlib.util.spec_from_file_location(fname, f"{BASE}/{fname}.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, node_name)

def run(label, fn, state):
    print(f"\n{'━'*62}")
    print(f"  {label.upper()}")
    print(f"{'━'*62}")
    t0 = time.time()
    try:
        result  = fn(state)
        elapsed = round(time.time() - t0, 2)
        output  = ""
        for key, val in result.items():
            if key not in ("workflow_id","timestamp","agent","error") and isinstance(val, str) and len(val) > 100:
                output = val; break
        print(f"  ✓ PASS  {elapsed}s")
        print(f"  → {output[:500].replace(chr(10),' ')}...")
        RESULTS.append({"agent": label, "status": "PASS", "elapsed": elapsed})
    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        print(f"  ✗ FAIL  {elapsed}s  — {type(e).__name__}: {str(e)[:300]}")
        RESULTS.append({"agent": label, "status": "FAIL", "elapsed": elapsed, "error": str(e)[:200]})

# ── TEST 1: copywriter ────────────────────────────────────────────────────────
fn = load_agent("copywriter", "copywriter_node")
run("copywriter", fn, {
    **base("copywriter"),
    "task": "Write conversion copy for a SaaS project management tool targeting freelancers",
    "brand_context": "Modern, no-nonsense, built for solo operators done with bloated tools",
    "output_type": "headline_variants",
    "copy_format": "direct_response",
    "copy_output": "", "headline": "",
})

# ── TEST 2: venture_ideator ───────────────────────────────────────────────────
fn = load_agent("venture_ideator", "venture_ideator_node")
run("venture_ideator", fn, {
    **base("venture_ideator"),
    "idea_context": "AI invoice chaser — automatically follows up overdue invoices via email for freelancers and agencies. Integrates with Xero and QuickBooks.",
    "idea_type": "saas_product",
    "market_size": "niche",
    "budget_hint": "lean",
    "venture_blueprint": "", "viability_score": 0,
})

# ── TEST 3: truth_verifier ────────────────────────────────────────────────────
fn = load_agent("truth_verifier", "truth_verifier_node")
run("truth_verifier", fn, {
    **base("truth_verifier"),
    "artifact": "Our AI tool increases revenue by 300% guaranteed. Trusted by thousands of industry-leading companies. The best solution on the market. Results in just 24 hours. It goes without saying that you need this.",
    "artifact_type": "marketing_copy",
    "check_level": "standard_audit",
    "verification_report": "", "gates_passed": 0, "gates_failed": 0, "confidence": "",
})

# ── TEST 4: ui_designer ───────────────────────────────────────────────────────
fn = load_agent("ui_designer", "ui_designer_node")
run("ui_designer", fn, {
    **base("ui_designer"),
    "task": "Design a 3-tier pricing card component — dark theme, Tailwind CSS v4, Framer Motion hover animations. Starter £29/mo, Pro £79/mo highlighted, Enterprise custom.",
    "design_context": "Dark industrial theme, primary #6366f1 indigo, bold and technical brand",
    "output_type": "component_spec",
    "component_type": "pricing",
    "design_spec": "", "component_code": "",
})

# ── TEST 5: fact_checker ──────────────────────────────────────────────────────
fn = load_agent("fact_checker", "fact_checker_node")
run("fact_checker", fn, {
    **base("fact_checker"),
    "claim": "Python is the most popular programming language in the world, used by over 50% of all developers globally",
    "supporting_context": "",
    "output_type": "claim_verification",
    "domain": "technology",
    "fact_check_report": "", "verdict": "",
})

# ── TEST 6: process_auditor ───────────────────────────────────────────────────
fn = load_agent("process_auditor", "process_auditor_node")
run("process_auditor", fn, {
    **base("process_auditor"),
    "process_description": "Client signs contract → manually email welcome PDF → client fills Google Form → copy-paste into CRM → PM manually creates tasks in Notion → first call booked by email back and forth for 3 days",
    "process_type": "client_onboarding",
    "output_type": "friction_report",
    "audit_report": "", "friction_count": 0, "bottleneck_score": 0,
})

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f"\n{'━'*62}")
print("  ORCHESTRA TEST COMPLETE")
print(f"{'━'*62}")
passed = sum(1 for r in RESULTS if r["status"] == "PASS")
print(f"  {passed}/{len(RESULTS)} PASSED  |  {len(RESULTS)-passed} FAILED\n")
for r in RESULTS:
    icon = "✓" if r["status"] == "PASS" else "✗"
    err  = f"  → {r['error']}" if r.get("error") else ""
    print(f"  {icon}  {r['agent']:30s}  {r['elapsed']}s{err}")
print()
