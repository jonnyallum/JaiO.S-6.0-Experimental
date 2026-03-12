#!/usr/bin/env python3
"""Fix TypedDict syntax errors in ui_designer.py and ux_researcher.py, then restart server."""
import subprocess, os, sys

BASE = "/home/jonny/antigravity/jaios6/agents"

fixes = {
    "ui_designer.py": [
        ("    design_output (str):      str", "    design_output:      str"),
        ("    components (str):      str",    "    components:      str"),
    ],
    "ux_researcher.py": [
        ("    research_output (str):      str",  "    research_output:      str"),
        ("    recommendations (str):      str",  "    recommendations:      str"),
    ],
}

for fname, replacements in fixes.items():
    fpath = os.path.join(BASE, fname)
    with open(fpath, "r") as f:
        content = f.read()
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            print(f"  FIXED: {fname}: {old.strip()} -> {new.strip()}")
        else:
            print(f"  SKIP:  {fname}: already fixed or not found: {old.strip()}")
    with open(fpath, "w") as f:
        f.write(content)

# Verify syntax
import py_compile
for fname in fixes:
    fpath = os.path.join(BASE, fname)
    try:
        py_compile.compile(fpath, doraise=True)
        print(f"  SYNTAX OK: {fname}")
    except py_compile.PyCompileError as e:
        print(f"  SYNTAX ERROR: {fname}: {e}")
        sys.exit(1)

# Restart server
print("\nRestarting jaios6 server...")
subprocess.run(["sudo", "systemctl", "restart", "jaios6"], check=True)
print("Server restarting... waiting 5s")
import time
time.sleep(5)

# Test
result = subprocess.run(["curl", "-s", "http://localhost:8765/agents"], capture_output=True, text=True)
import json
data = json.loads(result.stdout)
print(f"\nAgents loaded via API: {data['count']}")

# Check if the 11 are now present
on_disk = set(f.replace(".py","") for f in os.listdir(BASE) if f.endswith(".py") and f != "__init__.py")
loaded = set(a.replace("_node","") for a in data["agents"])
missing = sorted(on_disk - loaded)
if missing:
    print(f"STILL MISSING ({len(missing)}): {missing}")
else:
    print("ALL AGENTS LOADED SUCCESSFULLY!")
