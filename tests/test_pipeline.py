"""Test pipeline engine — chaining, eval gate, error handling."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPipelineEngine:
    def test_import(self):
        """Pipeline engine module should import cleanly."""
        from graphs.pipeline_engine import run_pipeline
        assert callable(run_pipeline)

    def test_unknown_pipeline_returns_error(self):
        """Requesting a nonexistent pipeline should return an error, not crash."""
        from graphs.pipeline_engine import run_pipeline
        result = run_pipeline("this_does_not_exist", "test task", eval_output=False)
        assert result["error"] is not None
        assert "Unknown pipeline" in result["error"]

    def test_eval_gate_import(self):
        """Eval gate module should import cleanly."""
        from graphs.eval_gate import evaluate_output
        assert callable(evaluate_output)

    def test_eval_gate_empty_output(self):
        """Eval gate should fail on empty output."""
        from graphs.eval_gate import evaluate_output
        result = evaluate_output("test task", "")
        assert result["pass"] is False
        assert result["score"] == 0
