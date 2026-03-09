"""Tests for @hugo — Phase 1 success criteria.

Run:
    pytest tests/test_hugo.py -v
    python tests/test_hugo.py        # quick run without pytest
"""

import pytest
from agents.hugo import hugo_node, HugoState


# ---------------------------------------------------------------------------
# Test 1: Graceful failure on invalid repo (success criterion #5)
# ---------------------------------------------------------------------------

def test_hugo_invalid_repo_raises():
    """@hugo must raise (not silently fail) when the repo doesn't exist."""
    state: HugoState = {
        "repo_owner": "this-repo-owner-does-not-exist-xyzabc123",
        "repo_name":  "fake-repo-does-not-exist-xyzabc",
        "query":      "test",
        "intelligence": "",
        "error": None,
    }

    with pytest.raises(Exception) as exc_info:
        hugo_node(state)

    # Should be an HTTP error (404) or similar
    print(f"\n  Graceful failure: {type(exc_info.value).__name__}: {exc_info.value}")


# ---------------------------------------------------------------------------
# Test 2: Real GitHub intelligence on a live public repo (criterion #1)
# ---------------------------------------------------------------------------

def test_hugo_real_repo():
    """@hugo returns real intelligence — not stub data — for a public repo."""
    state: HugoState = {
        "repo_owner": "jonnyallum",
        "repo_name":  "Antigravity_Orchestra",
        "query":      "What language is this project primarily written in and what does it do?",
        "intelligence": "",
        "error": None,
    }

    result = hugo_node(state)

    assert result["intelligence"], "Intelligence must not be empty"
    assert "STUB" not in result["intelligence"], "Must not return stub data"
    assert len(result["intelligence"]) > 100, "Intelligence must be substantial (>100 chars)"
    assert result["error"] is None, "error field must be None on success"

    print(f"\n  ✅ @hugo returned {len(result['intelligence'])} chars")
    print(f"  Sample: {result['intelligence'][:200]}...")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest needed for quick smoke test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("--- test_hugo_invalid_repo_raises ---")
    try:
        test_hugo_invalid_repo_raises()
        print("✅ Raised as expected")
    except AssertionError as e:
        print(f"❌ {e}")

    print("\n--- test_hugo_real_repo ---")
    try:
        test_hugo_real_repo()
        print("✅ Real intelligence returned")
    except AssertionError as e:
        print(f"❌ {e}")
