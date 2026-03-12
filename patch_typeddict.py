#!/usr/bin/env python3
"""
JaiOS 6.0 — Surgical TypedDict Patch
Adds `from __future__ import annotations` and `from typing import TypedDict`
to the 20 Gen-1 agents that use BaseState instead of TypedDict.
Preserves all existing code — only inserts missing imports.
"""
import os, re

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

TARGETS = [
    "architecture_review", "automation_architect", "brief_writer",
    "business_intelligence", "code_reviewer", "competitor_monitor",
    "content_scaler", "data_extraction", "dependency_audit",
    "email_architect", "funnel_architect", "github_intelligence",
    "monetisation_strategist", "quality_validation", "sales_conversion",
    "security_audit", "seo_specialist", "social_post_generator",
    "supabase_intelligence", "video_brief_writer",
]

patched = 0
skipped = 0

for name in TARGETS:
    path = os.path.join(AGENTS_DIR, f"{name}.py")
    if not os.path.exists(path):
        print(f"  SKIP (not found): {name}")
        skipped += 1
        continue

    with open(path) as f:
        code = f.read()

    changes = []

    # 1. Add `from __future__ import annotations` after the docstring
    if "from __future__ import annotations" not in code:
        # Find end of module docstring
        match = re.search(r'("""[\s\S]*?""")', code)
        if match:
            insert_pos = match.end()
            code = code[:insert_pos] + "\n\nfrom __future__ import annotations" + code[insert_pos:]
            changes.append("+ from __future__ import annotations")

    # 2. Add TypedDict import
    if "TypedDict" not in code:
        # Find the import block — insert after the last `from` or `import` line before ROLE =
        role_match = re.search(r'^ROLE\s*=', code, re.MULTILINE)
        if role_match:
            # Find the last import line before ROLE
            before_role = code[:role_match.start()]
            import_lines = list(re.finditer(r'^(?:from|import)\s+.+$', before_role, re.MULTILINE))
            if import_lines:
                last_import_end = import_lines[-1].end()
                code = code[:last_import_end] + "\nfrom typing import TypedDict" + code[last_import_end:]
                changes.append("+ from typing import TypedDict")

    if changes:
        with open(path, "w") as f:
            f.write(code)
        patched += 1
        print(f"  ✅ PATCHED: {name}")
        for c in changes:
            print(f"             {c}")
    else:
        skipped += 1
        print(f"  ⏭️  ALREADY OK: {name}")

print(f"\nDone: {patched} patched, {skipped} skipped")
