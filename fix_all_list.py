#!/usr/bin/env python3
"""Add missing agents to __all__ in agents/__init__.py, then restart."""
import subprocess, time

INIT = "/home/jonny/antigravity/jaios6/agents/__init__.py"

with open(INIT, "r") as f:
    content = f.read()

# New agents to add to __all__
new_entries = """    # ── Batch 6: awesome-llm-apps inspired ──
    "ux_researcher_node",
    "senior_developer_node",
    "system_architect_node",
    "investment_analyst_node",
    "recruitment_specialist_node",
    "sales_intelligence_node",
    "legal_analyst_node",
    "due_diligence_analyst_node",
    "deep_researcher_node",
    "product_launch_strategist_node",
    "financial_planner_node",
"""

# Insert before the closing ] of __all__
# Find the last ] that closes __all__
old = '    "ab_test_designer_node",      "ABTestState",\n]'
new = '    "ab_test_designer_node",      "ABTestState",\n' + new_entries + ']'

if old in content:
    content = content.replace(old, new)
    print("Added 11 new agents to __all__")
else:
    print("ERROR: Could not find insertion point")
    # Try alternative
    import sys
    sys.exit(1)

# Also check product_launch_strategist and financial_planner imports
if "product_launch_strategist" not in content:
    content += "\nfrom agents.product_launch_strategist import product_launch_strategist_node\n"
    print("Added product_launch_strategist import")

if "financial_planner" not in content:
    content += "from agents.financial_planner import financial_planner_node\n"
    print("Added financial_planner import")

with open(INIT, "w") as f:
    f.write(content)

# Verify import
print("\nVerifying import chain...")
import sys, importlib
sys.path.insert(0, "/home/jonny/antigravity/jaios6")
mods = [k for k in sys.modules if k.startswith("agents")]
for m in mods:
    del sys.modules[m]
mod = importlib.import_module("agents")
all_list = mod.__all__
nodes = [x for x in all_list if x.endswith("_node")]
print(f"  __all__ has {len(nodes)} node entries")

# Restart
print("\nRestarting server...")
subprocess.run(["sudo", "systemctl", "restart", "jaios6"], check=True)
print("Waiting 15s...")
time.sleep(15)

import json
result = subprocess.run(["curl", "-s", "http://localhost:8765/agents"], capture_output=True, text=True, timeout=10)
data = json.loads(result.stdout)
print(f"\n=== API RESULT: {data['count']} agents loaded ===")
if data["count"] >= 72:
    print("ALL AGENTS REGISTERED!")
else:
    print(f"Expected 72, got {data['count']}")
