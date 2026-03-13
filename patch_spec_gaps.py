#!/usr/bin/env python3
"""
JaiOS 6.0 — Spec Gap Patcher
Adds 4 missing patterns to all non-compliant agents:
  1. def _is_transient(exc) helper
  2. _generate = <existing retry func> alias
  3. "error": None (replaces "error": "")
  4. "agent": ROLE in success return
"""
import os, re, sys

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

IS_TRANSIENT_BLOCK = '''
def _is_transient(exc: BaseException) -> bool:
    \"\"\"TRANSIENT = 429 rate limit or 529 overload — safe to retry.\"\"\"
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)

'''

def needs_patch(code: str) -> dict:
    return {
        "is_transient":  not re.search(r'def _is_transient', code),
        "generate_func": not re.search(r'def _generate\(', code),
        "error_none":    bool(re.search(r'"error":\s*""', code)),
        "agent_role":    not re.search(r'"agent":\s*ROLE', code),
    }


def find_retry_func_name(code: str) -> str | None:
    """Find the name of the @retry-decorated function."""
    m = re.search(r'@retry\([\s\S]*?\)\s*def (\w+)\(', code)
    return m.group(1) if m else None


def patch_file(filepath: str, dry_run: bool = False) -> tuple[str, list[str]]:
    name = os.path.basename(filepath).replace(".py", "")
    with open(filepath) as f:
        code = f.read()

    gaps = needs_patch(code)
    if not any(gaps.values()):
        return name, []

    applied = []
    original = code

    # 1. Fix "error": "" → "error": None in success returns
    if gaps["error_none"]:
        # Only replace in success-context returns (not error strings)
        code = re.sub(r'"error":\s*""', '"error": None', code)
        applied.append('error_return: "error": "" → None')

    # 2. Add "agent": ROLE to success return blocks
    if gaps["agent_role"]:
        # Find return dicts that contain "error": None but NOT "agent"
        # Pattern: return { ... "error": None ... } without "agent"
        def add_agent_to_return(m):
            block = m.group(0)
            if '"agent"' in block or 'PERMANENT' in block or 'TRANSIENT' in block or 'UNEXPECTED' in block:
                return block
            # Add "agent": ROLE before "error": None
            return block.replace('"error": None', '"agent":  ROLE,\n        "error":  None')
        code = re.sub(
            r'return \{[\s\S]*?"error":\s*None[\s\S]*?\}',
            add_agent_to_return,
            code
        )
        if '"agent":  ROLE' in code or '"agent": ROLE' in code:
            applied.append('agent_return: added "agent": ROLE to success return')

    # 3. Add _is_transient before @retry block
    if gaps["is_transient"]:
        # Insert before first @retry
        code = re.sub(r'(\n@retry\()', IS_TRANSIENT_BLOCK + r'\1', code, count=1)
        applied.append("is_transient: added _is_transient() helper")

    # 4. Add _generate alias after the retry-decorated function
    if gaps["generate_func"]:
        func_name = find_retry_func_name(code)
        if func_name and func_name != "_generate":
            # Find end of that function (next def or class at same indent)
            # Add alias after it
            alias = f"\n_generate = {func_name}  # spec alias\n"
            # Insert after the function's first return statement block
            pattern = rf'(def {re.escape(func_name)}\([\s\S]*?)(\n\n\n|\ndef |\nclass )'
            def add_alias(m):
                return m.group(1) + alias + m.group(2)
            new_code = re.sub(pattern, add_alias, code, count=1)
            if new_code != code:
                code = new_code
                applied.append(f"generate_func: _generate = {func_name} alias added")

    if not dry_run and code != original:
        with open(filepath, "w") as f:
            f.write(code)

    return name, applied


def main():
    dry_run = "--dry-run" in sys.argv
    agents = sorted([
        os.path.join(AGENTS_DIR, f)
        for f in os.listdir(AGENTS_DIR)
        if f.endswith(".py") and f != "__init__.py"
    ])

    print(f"JaiOS 6.0 — Spec Gap Patcher {'(DRY RUN)' if dry_run else ''}")
    print(f"{'='*60}")

    total_patched = 0
    for filepath in agents:
        name, applied = patch_file(filepath, dry_run=dry_run)
        if applied:
            total_patched += 1
            print(f"\n  ✏️  {name}")
            for a in applied:
                print(f"     + {a}")

    print(f"\n{'='*60}")
    print(f"  Agents patched: {total_patched}/{len(agents)}")
    if dry_run:
        print("  (dry run — no files written)")


if __name__ == "__main__":
    main()
