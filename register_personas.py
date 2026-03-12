#!/usr/bin/env python3
"""Register 12 new agents in personas.json"""
import json, os
os.chdir("/home/jonny/antigravity/jaios6")

with open("personas/personas.json") as f:
    personas = json.load(f)

new = {
    "ui_designer": {"name": "UI Designer", "nickname": "ui_designer", "personality": "meticulous visual design", "description": "UI visual design specialist"},
    "ux_researcher": {"name": "UX Researcher", "nickname": "ux_researcher", "personality": "empathetic user research", "description": "UX research and usability specialist"},
    "senior_developer": {"name": "Senior Developer", "nickname": "senior_developer", "personality": "pragmatic full-stack engineering", "description": "Senior full-stack development specialist"},
    "system_architect": {"name": "System Architect", "nickname": "system_architect", "personality": "systems-thinking infrastructure design", "description": "System architecture specialist"},
    "investment_analyst": {"name": "Investment Analyst", "nickname": "investment_analyst", "personality": "rigorous financial analysis", "description": "Investment analysis specialist"},
    "recruitment_specialist": {"name": "Recruitment Specialist", "nickname": "recruitment_specialist", "personality": "strategic talent acquisition", "description": "Recruitment specialist"},
    "sales_intelligence": {"name": "Sales Intelligence", "nickname": "sales_intelligence", "personality": "data-driven sales strategy", "description": "Sales intelligence specialist"},
    "legal_analyst": {"name": "Legal Analyst", "nickname": "legal_analyst", "personality": "precise legal analysis", "description": "Legal analysis specialist"},
    "due_diligence_analyst": {"name": "Due Diligence Analyst", "nickname": "due_diligence_analyst", "personality": "thorough investigative analysis", "description": "Due diligence specialist"},
    "deep_researcher": {"name": "Deep Researcher", "nickname": "deep_researcher", "personality": "academically rigorous synthesis", "description": "Deep research specialist"},
    "product_launch_strategist": {"name": "Product Launch Strategist", "nickname": "product_launch_strategist", "personality": "strategic go-to-market planning", "description": "Product launch strategy specialist"},
    "financial_planner": {"name": "Financial Planner", "nickname": "financial_planner", "personality": "analytical financial planning", "description": "Financial planning specialist"},
}

personas.update(new)
with open("personas/personas.json", "w") as f:
    json.dump(personas, f, indent=2)
print(f"Updated personas.json: {len(personas)} total agents")
