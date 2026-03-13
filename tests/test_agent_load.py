#!/usr/bin/env python3
"""Smoke test: verify every agent module loads without import errors."""
import os
import importlib
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")

def get_agent_modules():
    return [f[:-3] for f in sorted(os.listdir(AGENTS_DIR))
            if f.endswith(".py") and f != "__init__.py"]

@pytest.mark.parametrize("module_name", get_agent_modules())
def test_agent_loads(module_name):
    """Each agent module should import without errors."""
    mod = importlib.import_module(f"agents.{module_name}")
    assert hasattr(mod, "build_graph"), f"{module_name} missing build_graph()"
    node_func = f"{module_name}_node"
    assert hasattr(mod, node_func), f"{module_name} missing {node_func}()"

@pytest.mark.parametrize("module_name", get_agent_modules())
def test_agent_has_role(module_name):
    """Each agent should define ROLE constant."""
    mod = importlib.import_module(f"agents.{module_name}")
    assert hasattr(mod, "ROLE"), f"{module_name} missing ROLE"
    assert isinstance(mod.ROLE, str), f"{module_name}.ROLE should be str"

def test_agent_count():
    """Verify we have the expected number of agents."""
    modules = get_agent_modules()
    assert len(modules) >= 90, f"Expected 90+ agents, got {len(modules)}"
