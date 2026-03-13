"""Test code executor — sandbox, timeout, security."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.code_executor import execute_python, execute_and_summarize


class TestCodeExecutor:
    def test_simple_code(self):
        """Basic Python execution should work."""
        result = execute_python("print(2 + 2)")
        assert result["returncode"] == 0
        assert "4" in result["stdout"]

    def test_empty_code_returns_error(self):
        """Empty code should return an error, not crash."""
        result = execute_python("")
        assert result["error"] is not None

    def test_syntax_error_captured(self):
        """Syntax errors should be captured in stderr."""
        result = execute_python("def (broken")
        assert result["returncode"] != 0

    def test_summarize_format(self):
        """execute_and_summarize should return formatted string."""
        result = execute_and_summarize("print('hello world')")
        assert "hello world" in result
