#!/usr/bin/env python3
"""
JaiOS 6.0 — 19-Point Spec Compliance Validator v2
Accepts ALL valid import conventions (root shims, utils/, tools/)
Focuses on REAL structural compliance, not import path bikeshedding.
"""
import os, re, sys

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

# ── 19-Point Spec Checklist (multi-path tolerant) ──────────────────────────────
CHECKS = [
    # (name, regex_pattern, description, critical)
    ("docstring",          r'"""[\s\S]*?"""',                          "Module docstring", True),
    ("node_contract",      r'(Node Contract|AGENT\s*:|Inputs?\s*:|Input keys)',  "Node Contract documented", True),
    ("loop_policy",        r'(Loop Policy|MAX_RETRIES|No iterative)',   "Loop/retry policy", True),
    ("failure_discrim",    r'(Failure Discrimination|PERMANENT|TRANSIENT|UNEXPECTED)', "Failure discrimination", True),
    ("checkpoint_doc",     r'(Checkpoint Semantics|PRE|POST)',          "Checkpoint semantics documented", False),
    ("future_annotations", r'from __future__ import annotations',      "__future__ annotations", False),
    ("typeddict",          r'(from typing import.*TypedDict|from typing_extensions import.*TypedDict|TypedDict)', "TypedDict usage", True),
    ("anthropic",          r'import anthropic',                        "Anthropic SDK", True),
    ("apistatus",          r'(APIStatusError|APIConnectionError|RateLimitError)', "API error class import", True),
    ("tenacity",           r'from tenacity import',                    "Tenacity retry", True),
    ("persona",            r'from personas.config import get_persona', "Persona config", True),
    ("metrics",            r'(CallMetrics|from.*metrics import|from tools.telemetry)', "Metrics/telemetry", True),
    ("checkpoint_util",    r'(from.*checkpoints? import|SupabaseStateLogger|checkpoint)', "Checkpoint utility", True),
    ("role_constant",      r'ROLE\s*=',                                "ROLE constant", True),
    ("max_retries",        r'MAX_RETRIES\s*=',                         "MAX_RETRIES constant", True),
    ("max_tokens",         r'MAX_TOKENS\s*=',                          "MAX_TOKENS constant", True),
    ("state_class",        r'(class \w+State|BaseState|TypedDict)',     "State class/type", True),
    ("node_function",      r'def \w+_node\(state',                     "Node function", True),
    ("retry_decorator",    r'@retry\(',                                "@retry decorator", True),
    ("error_handling",     r'(except.*Exception|except.*Error|"error")', "Error handling", True),
    ("agent_field",        r'("agent"|\'agent\'|ROLE)',                 "Agent identity in output", False),
    ("permanent_guard",    r'(raise ValueError|ValueError|PERMANENT)', "Permanent failure guard", True),
    ("domain_knowledge",   r'(VALID_|_GUIDANCE|_FRAMEWORKS|_CHECKLIST|ARCH_FILES|dict\[)', "Domain knowledge constants", False),
]


def validate_agent(filepath: str) -> tuple[str, int, int, int, list[str]]:
    name = os.path.basename(filepath).replace(".py", "")
    with open(filepath) as f:
        code = f.read()

    critical_fails = []
    non_crit_fails = []
    passes = 0
    
    for check_name, pattern, desc, critical in CHECKS:
        if re.search(pattern, code, re.MULTILINE):
            passes += 1
        else:
            if critical:
                critical_fails.append(f"CRITICAL: {check_name} — {desc}")
            else:
                non_crit_fails.append(f"minor: {check_name} — {desc}")

    return name, passes, len(CHECKS), len(critical_fails), critical_fails + non_crit_fails


def main():
    agents = sorted([
        os.path.join(AGENTS_DIR, f) 
        for f in os.listdir(AGENTS_DIR) 
        if f.endswith(".py") and f != "__init__.py"
    ])

    total = len(agents)
    full_pass = []
    minor_only = []
    critical_fail = []

    print(f"{'='*70}")
    print(f"  JaiOS 6.0 — Spec Compliance Audit v2 (multi-path tolerant)")
    print(f"  Agents: {total}")
    print(f"{'='*70}\n")

    for filepath in agents:
        name, score, max_s, n_crit, failures = validate_agent(filepath)
        pct = int(100 * score / max_s)

        if n_crit == 0 and not failures:
            full_pass.append(name)
            print(f"  ✅ PASS       {name} [{score}/{max_s}]")
        elif n_crit == 0:
            minor_only.append((name, failures))
            print(f"  ⚠️  MINOR     {name} [{score}/{max_s}]")
            for f in failures:
                print(f"                └─ {f}")
        else:
            critical_fail.append((name, n_crit, failures))
            print(f"  ❌ CRITICAL   {name} [{score}/{max_s}] ({n_crit} critical)")
            for f in failures:
                print(f"                └─ {f}")

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Total:           {total}")
    print(f"  ✅ Full pass:    {len(full_pass)}")
    print(f"  ⚠️  Minor only:  {len(minor_only)}")
    print(f"  ❌ Critical:     {len(critical_fail)}")

    if critical_fail:
        print(f"\n  Agents needing upgrade ({len(critical_fail)}):")
        for name, n, _ in critical_fail:
            print(f"     ❌ {name} ({n} critical gaps)")
    
    scores = []
    for filepath in agents:
        name, score, max_s, _, _ = validate_agent(filepath)
        scores.append(int(100 * score / max_s))
    
    print(f"\n  Avg compliance:  {sum(scores)/len(scores):.0f}%")
    print(f"  Min compliance:  {min(scores)}%")
    
    sys.exit(1 if critical_fail else 0)


if __name__ == "__main__":
    main()
