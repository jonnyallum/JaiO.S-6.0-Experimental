"""Test agent state contracts — every agent file must parse and export correctly."""
import pytest
import importlib
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def get_agent_modules():
    """Discover all agent .py files."""
    return [f.stem for f in AGENTS_DIR.glob("*.py") if f.stem != "__init__"]


class TestAgentContracts:
    def test_all_agents_parse(self):
        """Every agent file must be valid Python."""
        import ast
        errors = []
        for name in get_agent_modules():
            path = AGENTS_DIR / f"{name}.py"
            try:
                ast.parse(path.read_text())
            except SyntaxError as e:
                errors.append(f"{name}: {e}")
        assert not errors, f"Syntax errors: {errors}"

    def test_agent_count_minimum(self):
        """We should have at least 90 agents."""
        count = len(get_agent_modules())
        assert count >= 90, f"Only {count} agents found, expected 90+"

    def test_agents_have_state_class(self):
        """Spot-check: key agents should export a TypedDict State class."""
        # Just verify a few key agents have the expected pattern
        for agent_name in ["research_analyst", "code_reviewer", "seo_specialist"]:
            path = AGENTS_DIR / f"{agent_name}.py"
            if path.exists():
                content = path.read_text()
                assert "TypedDict" in content, f"{agent_name} missing TypedDict state"
                assert "def " in content, f"{agent_name} has no functions"
