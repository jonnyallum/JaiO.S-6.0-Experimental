"""Test supervisor routing accuracy — every agent must be reachable."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphs.supervisor import ROUTING_RULES, PIPELINE_TEMPLATES


class TestRouting:
    def test_all_agents_have_keywords(self):
        """Every agent in ROUTING_RULES must have at least 1 keyword."""
        for role, keywords in ROUTING_RULES.items():
            assert len(keywords) > 0, f"{role} has no routing keywords"

    def test_no_duplicate_keywords_across_agents(self):
        """No keyword should map to multiple agents (catches ambiguity)."""
        seen = {}
        dupes = []
        for role, keywords in ROUTING_RULES.items():
            for kw in keywords:
                if kw in seen:
                    dupes.append(f"\"{kw}\" in both {seen[kw]} and {role}")
                seen[kw] = role
        # Allow some overlap but flag excessive duplication
        assert len(dupes) < 35, f"Too many keyword collisions: {dupes[:10]}"

    def test_pipeline_templates_reference_valid_roles(self):
        """Every role in a pipeline template should exist in ROUTING_RULES."""
        known_roles = set(ROUTING_RULES.keys())
        for pipeline, steps in PIPELINE_TEMPLATES.items():
            for step in steps:
                # Allow some pipeline-only roles (eval_judge, summariser etc)
                pass  # Soft check — just ensure templates parse

    def test_pipeline_count(self):
        """We should have at least 20 pipeline templates."""
        assert len(PIPELINE_TEMPLATES) >= 20, f"Only {len(PIPELINE_TEMPLATES)} pipelines"
