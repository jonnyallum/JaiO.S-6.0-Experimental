#!/usr/bin/env python3
"""Fix ALL TypedDict 'field (str): str' syntax errors across all agent files."""
import os, re, py_compile, subprocess, time

BASE = "/home/jonny/antigravity/jaios6/agents"

# Pattern: "    some_field (str):      str" -> "    some_field:      str"
# Matches any word followed by space then (str) or (type) in a TypedDict context
PATTERN = re.compile(r'^(\s+\w+)\s+\(str\)(:)', re.MULTILINE)

total_fixes = 0
for fname in sorted(os.listdir(BASE)):
    if not fname.endswith('.py') or fname == '__init__.py':
        continue
    fpath = os.path.join(BASE, fname)
    with open(fpath, 'r') as f:
        content = f.read()
    
    matches = PATTERN.findall(content)
    if matches:
        new_content = PATTERN.sub(r'\1\2', content)
        with open(fpath, 'w') as f:
            f.write(new_content)
        fixes = len(matches)
        total_fixes += fixes
        print(f"  FIXED {fixes} in {fname}")
    
    # Verify syntax
    try:
        py_compile.compile(fpath, doraise=True)
    except py_compile.PyCompileError as e:
        print(f"  SYNTAX ERROR REMAINS: {fname}: {e}")

print(f"\nTotal fixes applied: {total_fixes}")

# Test full import chain
print("\nTesting import chain...")
try:
    import importlib, sys
    sys.path.insert(0, "/home/jonny/antigravity/jaios6")
    # Clear cached modules
    mods_to_clear = [k for k in sys.modules if k.startswith('agents')]
    for m in mods_to_clear:
        del sys.modules[m]
    
    mod = importlib.import_module("agents")
    node_fns = [x for x in dir(mod) if x.endswith('_node')]
    print(f"  agents package imports OK - {len(node_fns)} node functions exported")
except Exception as e:
    print(f"  IMPORT FAILED: {type(e).__name__}: {e}")

# Restart server
print("\nRestarting jaios6 server...")
subprocess.run(["sudo", "systemctl", "restart", "jaios6"], check=True)
print("Waiting 15s for Pi 5 boot...")
time.sleep(15)

# Test API
try:
    import json
    result = subprocess.run(["curl", "-s", "http://localhost:8765/agents"], 
                          capture_output=True, text=True, timeout=10)
    data = json.loads(result.stdout)
    count = data["count"]
    print(f"\n=== RESULT: {count} agents loaded via API ===")
    
    on_disk = set(f.replace(".py","") for f in os.listdir(BASE) 
                  if f.endswith(".py") and f != "__init__.py")
    loaded = set(a.replace("_node","") for a in data["agents"])
    missing = sorted(on_disk - loaded)
    if missing:
        print(f"STILL MISSING ({len(missing)}): {missing}")
    else:
        print("ALL AGENTS LOADED SUCCESSFULLY!")
except Exception as e:
    print(f"API check failed: {e}")
    print("Trying health endpoint...")
    r2 = subprocess.run(["curl", "-s", "http://localhost:8765/health"], 
                       capture_output=True, text=True, timeout=10)
    print(f"Health: {r2.stdout}")
