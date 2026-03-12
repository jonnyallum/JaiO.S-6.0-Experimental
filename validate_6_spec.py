#!/usr/bin/env python3
"""
JaiOS 6.0 — 19-Point Spec Compliance Validator
Checks EVERY agent in /agents/ against the full LangGraph spec.
Outputs: PASS/FAIL per agent with specific violations.
"""
import os, re, sys

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

# ── 19-Point Spec Checklist ────────────────────────────────────────────────────
CHECKS = [
    # (name, regex_pattern, description)
    ("docstring",          r'"""[\s\S]*?"""',                          "Module docstring present"),
    ("node_contract",      r'Node Contract:',                          "Node Contract documented"),
    ("loop_policy",        r'(Loop Policy:|MAX_RETRIES)',              "Loop/retry policy documented"),
    ("failure_discrim",    r'(Failure Discrimination:|PERMANENT|TRANSIENT|UNEXPECTED)', "Failure discrimination"),
    ("checkpoint_semantics", r'(Checkpoint Semantics:|PRE|POST)',      "Checkpoint semantics documented"),
    ("future_annotations", r'from __future__ import annotations',      "__future__ annotations import"),
    ("typeddict_import",   r'from typing import TypedDict',            "TypedDict import"),
    ("anthropic_import",   r'import anthropic',                        "Anthropic SDK import"),
    ("apistatus_import",   r'from anthropic import APIStatusError',    "APIStatusError import"),
    ("tenacity_import",    r'from tenacity import',                    "Tenacity retry import"),
    ("persona_import",     r'from personas.config import get_persona', "Persona config import"),
    ("metrics_import",     r'from utils.metrics import CallMetrics',   "CallMetrics import"),
    ("checkpoint_import",  r'from utils.checkpoints import checkpoint',"Checkpoint import"),
    ("role_constant",      r'^ROLE\s*=',                               "ROLE constant defined"),
    ("max_retries",        r'MAX_RETRIES\s*=',                         "MAX_RETRIES constant"),
    ("max_tokens",         r'MAX_TOKENS\s*=',                          "MAX_TOKENS constant"),
    ("state_typeddict",    r'class \w+State\(TypedDict',               "State TypedDict class"),
    ("node_function",      r'def \w+_node\(state',                     "Node function (xxx_node)"),
    ("retry_decorator",    r'@retry\(',                                "Tenacity @retry decorator"),
    ("checkpoint_pre",     r'checkpoint\("PRE"',                       "PRE checkpoint call"),
    ("checkpoint_post",    r'checkpoint\("POST"',                      "POST checkpoint call"),
    ("error_return",       r'"error":\s*None',                         "Error field in return"),
    ("agent_return",       r'"agent":\s*ROLE',                         "Agent field in return"),
    ("is_transient",       r'def _is_transient',                       "_is_transient function"),
    ("generate_func",      r'def _generate\(',                         "_generate function with retry"),
    ("permanent_guard",    r'(raise ValueError|PERMANENT)',            "Permanent failure guard"),
]


def validate_agent(filepath: str) -> tuple[str, list[str], list[str]]:
    """Validate a single agent file. Returns (name, passes, failures)."""
    name = os.path.basename(filepath).replace(".py", "")
    with open(filepath) as f:
        code = f.read()

    passes = []
    failures = []
    for check_name, pattern, desc in CHECKS:
        flags = re.MULTILINE if check_name == "role_constant" else 0
        if re.search(pattern, code, flags):
            passes.append(check_name)
        else:
            failures.append(f"{check_name}: {desc}")

    return name, passes, failures


def main():
    agents = sorted([
        os.path.join(AGENTS_DIR, f) 
        for f in os.listdir(AGENTS_DIR) 
        if f.endswith(".py") and f != "__init__.py"
    ])

    total = len(agents)
    full_pass = 0
    partial = 0
    critical_fails = []
    all_results = []

    print(f"{'='*70}")
    print(f"  JaiOS 6.0 — 19-Point Spec Compliance Audit")
    print(f"  Agents directory: {AGENTS_DIR}")
    print(f"  Total agents: {total}")
    print(f"{'='*70}\n")

    for filepath in agents:
        name, passes, failures = validate_agent(filepath)
        score = len(passes)
        max_score = len(CHECKS)
        pct = int(100 * score / max_score)

        if not failures:
            status = "✅ FULL PASS"
            full_pass += 1
        elif len(failures) <= 3:
            status = f"⚠️  PARTIAL ({pct}%)"
            partial += 1
        else:
            status = f"❌ FAIL ({pct}%)"
            critical_fails.append(name)

        all_results.append((name, score, max_score, pct, failures))
        
        if failures:
            print(f"  {status}  {name} [{score}/{max_score}]")
            for f in failures:
                print(f"           └─ MISSING: {f}")
        else:
            print(f"  {status}  {name} [{score}/{max_score}]")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Total agents:     {total}")
    print(f"  Full pass (100%): {full_pass}")
    print(f"  Partial:          {partial}")
    print(f"  Critical fails:   {len(critical_fails)}")
    
    if critical_fails:
        print(f"\n  ❌ Critical failures ({len(critical_fails)}):")
        for name in critical_fails:
            print(f"     - {name}")

    # Score distribution
    scores = [r[3] for r in all_results]
    avg = sum(scores) / len(scores) if scores else 0
    print(f"\n  Average compliance: {avg:.1f}%")
    print(f"  Min compliance:    {min(scores)}%")
    print(f"  Max compliance:    {max(scores)}%")

    # Return exit code based on critical fails
    sys.exit(1 if critical_fails else 0)


if __name__ == "__main__":
    main()
