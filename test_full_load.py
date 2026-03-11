"""
JaiOS 6.0 — Full Agent Load Test
Runtime-imports all 61 agents using importlib (bypasses __init__ settings chain).
Catches NameErrors, missing deps, bad module-level code.
"""
import sys, os, importlib.util
sys.path.insert(0, "/home/jonny/antigravity/jaios6")

from dotenv import load_dotenv
load_dotenv()

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

passed, failed = [], []

agent_files = sorted(
    f[:-3] for f in os.listdir(AGENTS_DIR)
    if f.endswith(".py") and f != "__init__.py"
)

print(f"\nRuntime load test — {len(agent_files)} agents\n{'─'*60}")

for name in agent_files:
    path = f"{AGENTS_DIR}/{name}.py"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Check the node function exists
        node_fn_name = f"{name}_node"
        if not hasattr(mod, node_fn_name):
            # Some agents may have differently named nodes
            callables = [x for x in dir(mod) if x.endswith("_node") and callable(getattr(mod, x))]
            if callables:
                node_fn_name = callables[0]
            else:
                raise AttributeError(f"No *_node function found (expected {name}_node)")

        # Check get_persona is used (not hardcoded)
        src = open(path).read()
        if "get_persona" not in src:
            raise ValueError("get_persona() not used — possible hardcoded identity")

        passed.append(name)
        print(f"  ✓  {name}")
    except Exception as e:
        failed.append((name, type(e).__name__, str(e)[:120]))
        print(f"  ✗  {name}  — {type(e).__name__}: {str(e)[:100]}")

print(f"\n{'─'*60}")
print(f"  {len(passed)}/{len(agent_files)} LOADED OK  |  {len(failed)} FAILED")
if failed:
    print("\nFailed agents:")
    for name, etype, emsg in failed:
        print(f"  {name}: [{etype}] {emsg}")
print()
