#!/usr/bin/env python3
"""Find which agents are on disk but NOT loading via the API."""
import json, os, sys, subprocess, importlib

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

# Get loaded agents from API
try:
    result = subprocess.run(["curl", "-s", "http://localhost:8765/agents"], capture_output=True, text=True)
    data = json.loads(result.stdout)
    loaded = set(a.replace("_node", "") for a in data["agents"])
except Exception as e:
    print(f"ERROR: Could not reach API: {e}")
    loaded = set()

# Get agents on disk
on_disk = set(
    f.replace(".py", "")
    for f in os.listdir(AGENTS_DIR)
    if f.endswith(".py") and f != "__init__.py"
)

missing = sorted(on_disk - loaded)

print(f"Loaded via API: {len(loaded)}")
print(f"On disk:        {len(on_disk)}")
print(f"Missing:        {len(missing)}")
print()

# Try importing each missing agent to find the error
sys.path.insert(0, "/home/jonny/antigravity/jaios6")
for name in missing:
    print(f"--- {name} ---")
    try:
        mod = importlib.import_module(f"agents.{name}")
        # Check if it has the _node function
        node_fn = f"{name}_node"
        if hasattr(mod, node_fn):
            print(f"  IMPORT OK, has {node_fn}() — likely not in __init__.py")
        else:
            fns = [x for x in dir(mod) if x.endswith("_node")]
            print(f"  IMPORT OK, but no {node_fn}(). Found: {fns}")
    except Exception as e:
        print(f"  IMPORT FAILED: {type(e).__name__}: {e}")
    print()
