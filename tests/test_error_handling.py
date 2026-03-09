"""
Phase 1 Error Handling Tests

Tests 4 scenarios per success criterion #5:
  1. Invalid repo → graceful failure (error state returned, no crash)
  2. GitHub API 403 → error state returned, not raised
  3. Supabase write failure → non-fatal, workflow continues
  4. Full graph with invalid repo → completes without unhandled exception

Run:
  pytest tests/test_error_handling.py -v
  python tests/test_error_handling.py   # standalone
"""
import uuid
from unittest.mock import patch, MagicMock

import pytest
from github import GithubException

from agents.github_intelligence import GitHubIntelState, github_intelligence_node
from graphs.test_graph import run_test


def _make_state(**overrides) -> GitHubIntelState:
    state: GitHubIntelState = {
        "workflow_id": str(uuid.uuid4()),
        "timestamp": "2026-03-09T00:00:00Z",
        "agent": "github_intelligence",
        "error": None,
        "repo_owner": "jonnyallum",
        "repo_name": "JaiO.S-6.0-Experimental",
        "query": "test",
        "intelligence": "",
    }
    state.update(overrides)
    return state


class TestErrorHandling:

    def test_1_invalid_repo_graceful_failure(self):
        """
        Invalid repo owner/name returns error dict, never raises.
        intelligence must be empty string.
        """
        state = _make_state(
            repo_owner="invalid-user-xyz-12345",
            repo_name="does-not-exist-xyz-12345",
        )
        result = github_intelligence_node(state)

        assert result.get("error") is not None, "Should have error for invalid repo"
        assert isinstance(result["error"], str)
        assert result.get("intelligence", "") == ""
        assert len(result["error"]) > 0
        print(f"\n  Error: {result['error'][:100]}")

    def test_2_github_api_403_handled(self):
        """
        GitHub API 403 (rate limit / permission) returns error state, not exception.
        """
        with patch("tools.github_tools.GitHubTools.get_repo") as mock_get:
            mock_get.side_effect = GithubException(
                status=403,
                data={"message": "API rate limit exceeded"},
                headers={},
            )
            state = _make_state()
            result = github_intelligence_node(state)

            assert result.get("error") is not None
            assert result.get("intelligence", "") == ""
            print(f"\n  GitHub 403 handled: {result['error'][:80]}")

    def test_3_supabase_write_failure_non_fatal(self):
        """
        Supabase write failure is logged but does NOT crash the skill.
        The workflow should still return a result (pass or fail independently).
        """
        with patch("tools.supabase_tools.SupabaseStateLogger.log_state") as mock_log:
            mock_log.side_effect = Exception("DB connection refused")
            state = _make_state()

            try:
                result = github_intelligence_node(state)
                # Must return a dict with at least intelligence or error
                assert isinstance(result, dict)
                assert "intelligence" in result or "error" in result
                print("\n  Supabase failure: non-fatal, workflow continued")
            except Exception as exc:
                pytest.fail(f"Supabase failure crashed the skill node: {exc}")

    def test_4_full_graph_invalid_repo_no_unhandled_exception(self):
        """
        Full graph execution with an invalid repo completes without raising.
        Output must be a dict with a 'passed' key.
        """
        output = run_test(
            repo_owner="invalid-org-xyz-99999",
            repo_name="nonexistent-xyz-99999",
            query="test",
        )
        assert output is not None
        assert isinstance(output, dict)
        assert "passed" in output
        assert "metrics" in output
        # It will not pass (invalid repo), but it should not crash
        print(f"\n  Full graph invalid repo: passed={output['passed']}, no exception")


if __name__ == "__main__":
    suite = TestErrorHandling()
    tests = [
        ("1. Invalid repo graceful failure", suite.test_1_invalid_repo_graceful_failure),
        ("2. GitHub API 403 handled", suite.test_2_github_api_403_handled),
        ("3. Supabase write failure non-fatal", suite.test_3_supabase_write_failure_non_fatal),
        ("4. Full graph invalid repo no exception", suite.test_4_full_graph_invalid_repo_no_unhandled_exception),
    ]
    print("\n=== Phase 1 Error Handling Tests ===\n")
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \u2705 {name}")
            passed += 1
        except Exception as exc:
            print(f"  \u274c {name}: {exc}")
    print(f"\n  {passed}/{len(tests)} passed\n")
