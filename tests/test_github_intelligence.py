"""
Unit tests for github_intelligence skill node.

Run:
  pytest tests/test_github_intelligence.py -v
  python tests/test_github_intelligence.py   # standalone
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from agents.github_intelligence import GitHubIntelState, github_intelligence_node


def _make_state(**overrides) -> GitHubIntelState:
    state: GitHubIntelState = {
        "workflow_id": str(uuid.uuid4()),
        "timestamp": "2026-03-09T00:00:00Z",
        "agent": "github_intelligence",
        "error": None,
        "repo_owner": "jonnyallum",
        "repo_name": "JaiO.S-6.0-Experimental",
        "query": "What is this repository about and what is its current status?",
        "intelligence": "",
    }
    state.update(overrides)
    return state


class TestGitHubIntelligence:

    def test_returns_intelligence_from_real_repo(self):
        """Happy path: skill returns substantive intelligence from a real repo."""
        state = _make_state()
        result = github_intelligence_node(state)

        assert "intelligence" in result
        assert len(result["intelligence"]) > 100, (
            f"Intelligence too short: {len(result['intelligence'])} chars"
        )
        assert result.get("error") is None
        assert result.get("workflow_id") == state["workflow_id"]
        print(f"\n  Intelligence: {len(result['intelligence']):,} chars")

    def test_state_keys_present(self):
        """Returned dict contains all expected keys."""
        state = _make_state()
        result = github_intelligence_node(state)
        for key in ("intelligence", "error", "workflow_id", "agent"):
            assert key in result, f"Missing key: {key}"

    def test_invalid_repo_returns_error_not_exception(self):
        """Invalid repo: returns error string, never raises."""
        state = _make_state(
            repo_owner="invalid-org-xyz-999",
            repo_name="nonexistent-repo-xyz-999",
        )
        result = github_intelligence_node(state)
        assert result.get("error") is not None
        assert result.get("intelligence", "") == ""
        print(f"\n  Error handled: {result['error'][:80]}")

    def test_supabase_failure_is_non_fatal(self):
        """Supabase write failure must not crash the skill."""
        with patch("tools.supabase_tools.SupabaseStateLogger.log_state") as mock_log:
            mock_log.side_effect = Exception("Supabase connection refused")
            state = _make_state()
            # Should not raise, regardless of Supabase
            try:
                result = github_intelligence_node(state)
                assert "intelligence" in result or "error" in result
                print("\n  Supabase failure correctly non-fatal")
            except Exception as exc:
                pytest.fail(f"Supabase failure crashed the skill: {exc}")


if __name__ == "__main__":
    suite = TestGitHubIntelligence()
    tests = [
        ("test_state_keys_present", suite.test_state_keys_present),
        ("test_returns_intelligence_from_real_repo", suite.test_returns_intelligence_from_real_repo),
        ("test_invalid_repo_returns_error_not_exception", suite.test_invalid_repo_returns_error_not_exception),
        ("test_supabase_failure_is_non_fatal", suite.test_supabase_failure_is_non_fatal),
    ]
    print("\n=== GitHub Intelligence Tests ===\n")
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \u2705 {name}")
            passed += 1
        except Exception as exc:
            print(f"  \u274c {name}: {exc}")
    print(f"\n  {passed}/{len(tests)} passed\n")
