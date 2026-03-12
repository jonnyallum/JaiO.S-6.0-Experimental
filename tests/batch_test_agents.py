#!/usr/bin/env python3
"""Batch e2e test for all 61 JaiOS 6.0 agents via POST /run."""
import json, time, sys, os
os.chdir("/home/jonny/antigravity/jaios6")
from dotenv import load_dotenv
load_dotenv()

import requests

BASE = "http://localhost:8765"
KEY = os.environ.get("JAIOS_API_KEY", "")
HEADERS = {"Content-Type": "application/json", "X-API-Key": KEY}

# Map every agent to a brief that should trigger exactly that role
AGENT_BRIEFS = {
    "ab_test_designer": "Design an A/B test for a landing page headline",
    "ad_copy_writer": "Write Facebook ad copy for a SaaS product launch",
    "agent_builder": "Create a spec for a new AI agent that monitors uptime",
    "analytics_reporter": "Generate a weekly analytics report for website traffic",
    "architecture_review": "Review the microservices architecture of an e-commerce platform",
    "automation_architect": "Design an n8n automation workflow for lead nurturing",
    "brand_voice_guide": "Create brand voice guidelines for a luxury fashion label",
    "brief_writer": "Write a project brief for a mobile app redesign",
    "business_intelligence": "Analyse Q4 revenue data and identify growth opportunities",
    "case_study_writer": "Write a case study about a successful cloud migration project",
    "chatbot_designer": "Design a customer support chatbot flow for a SaaS product",
    "code_reviewer": "Review this Python function for best practices: def add(a,b): return a+b",
    "competitor_monitor": "Analyse the competitive landscape for AI code assistants",
    "content_auditor": "Audit the blog content strategy for SEO effectiveness",
    "content_scaler": "Create a content scaling plan to produce 50 articles per month",
    "copywriter": "Write website copy for a fintech startup homepage",
    "course_designer": "Design a 6-week online course curriculum on Python for beginners",
    "creative_director": "Create a creative direction brief for a product rebrand campaign",
    "customer_success": "Design a customer success playbook for enterprise SaaS onboarding",
    "database_architect": "Design a PostgreSQL schema for a multi-tenant SaaS application",
    "data_extraction": "Extract structured data from this text: John Smith, CEO, Acme Corp, john@acme.com",
    "data_parser": "Parse and normalize this CSV data: name,age,city\\nJohn,30,London",
    "dependency_audit": "Audit npm dependencies for security vulnerabilities in a Node.js project",
    "deployment_specialist": "Create a deployment checklist for a Next.js app to Vercel",
    "devops_engineer": "Set up a CI/CD pipeline using GitHub Actions for a Python API",
    "ecommerce_strategist": "Develop an e-commerce strategy to increase conversion rate by 20%",
    "email_architect": "Design an email drip campaign for SaaS trial users",
    "fact_checker": "Verify this claim: Python was created by Guido van Rossum in 1991",
    "financial_analyst": "Analyse the financial viability of launching a subscription product at 29/month",
    "fullstack_architect": "Design the tech stack for a real-time collaboration platform",
    "funnel_architect": "Design a marketing funnel for a B2B lead generation campaign",
    "gcp_ai_specialist": "Recommend GCP AI services for a document processing pipeline",
    "github_intelligence": "Analyse trending GitHub repos in the AI agents space this week",
    "investor_pitch_writer": "Write an investor pitch deck outline for a Series A AI startup",
    "knowledge_base_writer": "Write a knowledge base article on how to reset a password",
    "launch_orchestrator": "Create a product launch plan for a new mobile app release",
    "legal_advisor": "Review GDPR compliance requirements for a SaaS platform storing EU user data",
    "mcp_builder": "Design an MCP server spec for integrating with the Stripe API",
    "monetisation_strategist": "Develop a monetisation strategy for a free developer tool",
    "performance_auditor": "Audit the performance of a React web application with slow load times",
    "persona_builder": "Create a detailed buyer persona for a B2B marketing automation tool",
    "pipeline_monitor": "Design a monitoring dashboard for a data processing pipeline",
    "pricing_strategist": "Develop a pricing strategy for a tiered SaaS product",
    "process_auditor": "Audit the software development process for efficiency improvements",
    "product_strategist": "Define a product strategy for entering the AI assistant market",
    "project_manager": "Create a project plan with milestones for a 3-month website rebuild",
    "proposal_writer": "Write a consulting proposal for a digital transformation project",
    "pr_writer": "Write a press release announcing a new AI product launch",
    "quality_validation": "Validate the quality of this API response format and suggest improvements",
    "research_analyst": "Research the current state of the AI agent framework market",
    "sales_conversion": "Develop a sales conversion strategy for enterprise software demos",
    "security_audit": "Perform a security audit checklist for a Node.js REST API",
    "seo_specialist": "Create an SEO strategy for a new tech blog targeting developers",
    "social_post_generator": "Generate 5 LinkedIn posts about AI automation for business",
    "supabase_intelligence": "Analyse Supabase usage patterns and recommend optimisations",
    "supabase_specialist": "Design a Supabase schema with RLS policies for a multi-user app",
    "truth_verifier": "Verify whether GPT-4 was released in March 2023",
    "ui_designer": "Design a UI layout for a dashboard showing real-time analytics",
    "venture_ideator": "Generate 5 AI startup ideas targeting the healthcare industry",
    "video_brief_writer": "Write a video production brief for a 60-second product demo",
    "voice_synthesiser": "Create a voice synthesis spec for a brand mascot character",
}

def test_agent(role, brief):
    """Send a brief to /run and check the response."""
    try:
        r = requests.post(
            f"{BASE}/run",
            headers=HEADERS,
            json={"brief": brief, "sync": True},
            timeout=120,
        )
        if r.status_code != 200:
            return {"status": "FAIL", "code": r.status_code, "error": r.text[:200]}
        
        data = r.json()
        output = data.get("output", {})
        error = output.get("error")
        result = output.get("result", "")
        selected = output.get("selected_role", "unknown")
        elapsed = data.get("elapsed", 0)
        
        if error:
            return {"status": "FAIL", "error": str(error)[:200], "elapsed": elapsed}
        if not result or len(str(result)) < 10:
            return {"status": "WEAK", "result_len": len(str(result)), "elapsed": elapsed, "selected": selected}
        
        return {"status": "PASS", "elapsed": round(elapsed, 1), "selected": selected, "result_preview": str(result)[:80]}
    except requests.Timeout:
        return {"status": "TIMEOUT"}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)[:200]}

# Run all tests
results = {}
passed = failed = weak = 0
total = len(AGENT_BRIEFS)

print(f"Testing {total} agents against POST /run (auth enabled)")
print("=" * 70)

for i, (role, brief) in enumerate(AGENT_BRIEFS.items(), 1):
    print(f"[{i}/{total}] {role}...", end=" ", flush=True)
    result = test_agent(role, brief)
    results[role] = result
    
    status = result["status"]
    if status == "PASS":
        passed += 1
        print(f"PASS ({result['elapsed']}s, routed→{result['selected']})")
    elif status == "WEAK":
        weak += 1
        print(f"WEAK (short result, {result.get('elapsed', '?')}s)")
    else:
        failed += 1
        print(f"FAIL: {result.get('error', result.get('code', '?'))[:60]}")

print("=" * 70)
print(f"RESULTS: {passed} PASS | {weak} WEAK | {failed} FAIL | {total} total")
print()

# Save results
with open("/home/jonny/antigravity/jaios6/tests/batch_results.json", "w") as f:
    json.dump({"summary": {"pass": passed, "weak": weak, "fail": failed, "total": total}, "details": results}, f, indent=2)

# Print failures for quick fix
if failed or weak:
    print("\n--- FAILURES/WEAK ---")
    for role, r in results.items():
        if r["status"] in ("FAIL", "WEAK", "TIMEOUT", "ERROR"):
            print(f"  {role}: {json.dumps(r)}")
