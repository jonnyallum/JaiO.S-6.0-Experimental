#!/usr/bin/env python3
"""Test routing rules integrity — every agent is routable, no phantoms."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from graphs.supervisor import ROUTING_RULES, PIPELINE_TEMPLATES

AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")

def get_agent_files():
    return {f[:-3] for f in os.listdir(AGENTS_DIR)
            if f.endswith(".py") and f != "__init__.py"}

def test_no_phantom_routes():
    """Every routing rule should point to an existing agent file."""
    agents = get_agent_files()
    phantoms = [r for r in ROUTING_RULES if r not in agents]
    assert phantoms == [], f"Phantom routes (no agent file): {phantoms}"

def test_no_unroutable_agents():
    """Every agent file should have routing rules."""
    agents = get_agent_files()
    unroutable = [a for a in agents if a not in ROUTING_RULES]
    assert unroutable == [], f"Unroutable agents (no routing rule): {unroutable}"

def test_no_phantom_pipeline_refs():
    """Every agent referenced in pipelines should exist."""
    agents = get_agent_files()
    all_refs = set()
    for agents_list in PIPELINE_TEMPLATES.values():
        all_refs.update(agents_list)
    phantoms = [r for r in all_refs if r not in agents]
    assert phantoms == [], f"Phantom pipeline refs: {phantoms}"

def test_routing_rules_have_keywords():
    """Every routing rule should have at least one keyword."""
    empty = [r for r, kw in ROUTING_RULES.items() if not kw]
    assert empty == [], f"Routes with empty keywords: {empty}"
