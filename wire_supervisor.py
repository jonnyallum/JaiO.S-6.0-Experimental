#!/usr/bin/env python3
"""Wire 11 new agents into supervisor.py: imports + routing + dispatch."""
import subprocess, time

SUPERVISOR = "/home/jonny/antigravity/jaios6/graphs/supervisor.py"

with open(SUPERVISOR, "r") as f:
    content = f.read()

# ── 1. IMPORTS ──────────────────────────────────────────────────────────────
new_imports = """from agents.ux_researcher import ux_researcher_node
from agents.senior_developer import senior_developer_node
from agents.system_architect import system_architect_node
from agents.investment_analyst import investment_analyst_node
from agents.recruitment_specialist import recruitment_specialist_node
from agents.sales_intelligence import sales_intelligence_node
from agents.legal_analyst import legal_analyst_node
from agents.due_diligence_analyst import due_diligence_analyst_node
from agents.deep_researcher import deep_researcher_node
from agents.product_launch_strategist import product_launch_strategist_node
from agents.financial_planner import financial_planner_node
"""

# Insert after last existing import (before `log = structlog.get_logger()`)
anchor = "from tools.notification_tools import TelegramNotifier\n"
if anchor in content and "from agents.deep_researcher" not in content:
    content = content.replace(anchor, anchor + new_imports)
    print("✅ Added 11 import lines")
else:
    print("⚠️  Imports already present or anchor not found")

# ── 2. ROUTING RULES ───────────────────────────────────────────────────────
new_routing = '''
    # ── Batch 6: awesome-llm-apps inspired ────────────────────────────────
    "ux_researcher": [
        "ux research", "user research", "usability", "user testing",
        "journey map", "user journey", "heuristic evaluation", "persona research",
        "user interview", "ux audit", "user experience",
    ],
    "senior_developer": [
        "senior dev", "code architecture", "tech debt", "refactor code",
        "code implementation", "write code", "implement feature", "coding",
        "debug code", "fix bug", "software development",
    ],
    "system_architect": [
        "system architecture", "infrastructure design", "microservices",
        "service mesh", "load balancing", "scalability", "system design",
        "distributed system", "high availability", "fault tolerance",
    ],
    "investment_analyst": [
        "investment analysis", "stock analysis", "portfolio", "market research",
        "financial modeling", "valuation", "equity research", "investment thesis",
        "asset allocation", "risk assessment investment",
    ],
    "recruitment_specialist": [
        "recruitment", "hiring", "talent acquisition", "job description",
        "interview", "candidate", "onboarding", "headhunt", "staffing",
        "job posting", "recruit",
    ],
    "sales_intelligence": [
        "sales intelligence", "prospect research", "lead scoring",
        "sales pipeline", "outreach strategy", "prospect analysis",
        "sales enablement", "competitive selling", "account research",
    ],
    "legal_analyst": [
        "legal analysis", "contract review", "legal risk", "compliance review",
        "regulatory", "legal due diligence", "terms of service", "privacy policy",
        "legal opinion", "contract clause",
    ],
    "due_diligence_analyst": [
        "due diligence", "company evaluation", "market validation",
        "risk scoring", "business assessment", "company research",
        "acquisition analysis", "dd report", "target evaluation",
    ],
    "deep_researcher": [
        "deep research", "literature review", "academic research",
        "systematic review", "evidence synthesis", "research paper",
        "meta-analysis", "comprehensive research", "in-depth research",
        "thorough research", "research report",
    ],
    "product_launch_strategist": [
        "product launch", "go to market", "gtm strategy", "launch plan",
        "launch sequence", "product release", "market entry",
        "launch checklist", "product rollout",
    ],
    "financial_planner": [
        "financial plan", "budget", "forecast", "cash flow",
        "financial projection", "expense tracking", "revenue forecast",
        "financial model", "break even", "profit margin", "financial planning",
    ],
'''

# Insert before the closing } of ROUTING_RULES — find the last entry
# Look for the pattern where ROUTING_RULES ends
if '"ux_researcher"' not in content:
    # Find the end of the last routing entry and add before it
    # We need to find the closing of ROUTING_RULES dict
    import re
    # Find all entries in routing rules, add after the last one
    # The dict ends with a } on its own line after the last entry
    # Let's find the pattern: last entry ends with ],\n}
    content = content.replace(
        "\n}\n\n\nclass SupervisorState",
        new_routing + "}\n\n\nclass SupervisorState"
    )
    print("✅ Added 11 routing rule entries")
else:
    print("⚠️  Routing rules already present")

# ── 3. DISPATCH BLOCKS ─────────────────────────────────────────────────────
dispatch_blocks = '''
    # ── Batch 6: awesome-llm-apps inspired agents ─────────────────────────

    elif role == "ux_researcher":
        r = ux_researcher_node({
            **base, "agent": role,
            "task": task, "research_type": "general",
            "ux_report": "",
        })
        return {"result": r.get("ux_report", ""), "error": r.get("error")}

    elif role == "senior_developer":
        r = senior_developer_node({
            **base, "agent": role,
            "task": task, "language": "python",
            "code_output": "",
        })
        return {"result": r.get("code_output", ""), "error": r.get("error")}

    elif role == "system_architect":
        r = system_architect_node({
            **base, "agent": role,
            "task": task, "scope": "general",
            "architecture_report": "",
        })
        return {"result": r.get("architecture_report", ""), "error": r.get("error")}

    elif role == "investment_analyst":
        r = investment_analyst_node({
            **base, "agent": role,
            "task": task, "analysis_type": "general",
            "investment_report": "",
        })
        return {"result": r.get("investment_report", ""), "error": r.get("error")}

    elif role == "recruitment_specialist":
        r = recruitment_specialist_node({
            **base, "agent": role,
            "task": task, "recruitment_type": "general",
            "recruitment_report": "",
        })
        return {"result": r.get("recruitment_report", ""), "error": r.get("error")}

    elif role == "sales_intelligence":
        r = sales_intelligence_node({
            **base, "agent": role,
            "task": task, "research_type": "general",
            "sales_report": "",
        })
        return {"result": r.get("sales_report", ""), "error": r.get("error")}

    elif role == "legal_analyst":
        r = legal_analyst_node({
            **base, "agent": role,
            "task": task, "analysis_type": "general",
            "legal_report": "",
        })
        return {"result": r.get("legal_report", ""), "error": r.get("error")}

    elif role == "due_diligence_analyst":
        r = due_diligence_analyst_node({
            **base, "agent": role,
            "task": task, "dd_type": "general",
            "dd_report": "",
        })
        return {"result": r.get("dd_report", ""), "error": r.get("error")}

    elif role == "deep_researcher":
        r = deep_researcher_node({
            **base, "agent": role,
            "task": task, "research_scope": "comprehensive",
            "research_report": "",
        })
        return {"result": r.get("research_report", ""), "error": r.get("error")}

    elif role == "product_launch_strategist":
        r = product_launch_strategist_node({
            **base, "agent": role,
            "task": task, "launch_phase": "planning",
            "launch_plan": "",
        })
        return {"result": r.get("launch_plan", ""), "error": r.get("error")}

    elif role == "financial_planner":
        r = financial_planner_node({
            **base, "agent": role,
            "task": task, "planning_type": "general",
            "financial_plan": "",
        })
        return {"result": r.get("financial_plan", ""), "error": r.get("error")}
'''

# Insert before the final else block in execute_node
if 'role == "ux_researcher"' not in content:
    # Find the fallback else: block
    content = content.replace(
        '    else:\n        return {"result": f"Role',
        dispatch_blocks + '    else:\n        return {"result": f"Role'
    )
    print("✅ Added 11 dispatch elif blocks")
else:
    print("⚠️  Dispatch blocks already present")

with open(SUPERVISOR, "w") as f:
    f.write(content)

print("\n🔄 Restarting server...")
subprocess.run(["sudo", "systemctl", "restart", "jaios6"], check=True)
print("⏳ Waiting 15s for startup...")
time.sleep(15)

# Verify
import json
result = subprocess.run(
    ["curl", "-s", "http://localhost:8765/health"],
    capture_output=True, text=True, timeout=10
)
print(f"\n🏥 Health: {result.stdout.strip()}")

# Count routing rules
with open(SUPERVISOR, "r") as f:
    text = f.read()

import re
rules = re.findall(r'"(\w+)":\s*\[', text[:text.index("def _classify_task")])
print(f"📊 Routing rules: {len(rules)} agents routable")
print(f"🎯 Target: 72 (61 original + 11 new)")
