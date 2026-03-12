#!/usr/bin/env python3
"""
Deploy 12 new JaiOS 6.0 agents stolen from awesome-llm-apps patterns,
rewritten to full 19-point LangGraph spec. NO human names.
"""
import os, textwrap

AGENTS_DIR = "/home/jonny/antigravity/jaios6/agents"

# ── Agent template factory ─────────────────────────────────────────────────────
def make_agent(role: str, description: str, inputs: str, outputs: str,
               valid_sets: str, domain_knowledge: str, prompt_body: str,
               output_keys: list[str], max_tokens: int = 2400) -> str:
    """Generate a full 19-point compliant agent file."""
    state_fields = "\n".join(f"    {k}: str" for k in ["workflow_id", "timestamp", "agent", "error"] + [o.split(":")[0].strip() for o in outputs.split(",")])
    output_return = ", ".join(f'"{k.strip()}": output' if i == 0 else f'"{k.strip()}": ""' for i, k in enumerate(output_keys))

    return textwrap.dedent(f'''
"""
{role.replace("_", " ").title()} - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : {inputs}
    Outputs: {outputs}
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task → ValueError (no retry)
    TRANSIENT  → HTTP 429/529 → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before analysis
    POST - output persisted after successful generation
"""

from __future__ import annotations

import re
from typing import TypedDict

import anthropic
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint

ROLE        = "{role}"
MAX_RETRIES = 3
MAX_TOKENS  = {max_tokens}

{valid_sets}

{domain_knowledge}


class {role.title().replace("_", "")}State(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
{chr(10).join("    " + k.strip().split(":")[0] + ":      str" for k in outputs.split(","))}


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {{persona['personality']}} specialist.

ROLE: {description}

TASK:
{{task}}

CONTEXT:
{{ctx or "None provided"}}

{prompt_body}
"""


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=MAX_TOKENS,
                                       messages=[{{"role": "user", "content": prompt}}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def {role}_node(state: dict) -> dict:
    thread_id = state.get("workflow_id", "local")
    task      = state.get("task", "").strip()

    if not task:
        raise ValueError("PERMANENT: task is required.")

    checkpoint("PRE", thread_id, ROLE, {{"task_len": len(task)}})

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_prompt(state)

    try:
        output = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {{exc.status_code}}: {{exc}}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {{type(exc).__name__}}: {{exc}}") from exc

    checkpoint("POST", thread_id, ROLE, {{"output_len": len(output)}})

    return {{**state, "agent": ROLE, {output_return}, "error": None}}
''').strip() + "\n"


# ── 12 New Agents ──────────────────────────────────────────────────────────────
AGENTS = {
    "ui_designer": {
        "description": "UI visual design specialist — component design, design systems, visual hierarchy, responsive layouts, accessibility-first design",
        "inputs": "task (str), context (str)",
        "outputs": "design_output (str), components (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_DESIGN_PRINCIPLES = {
    "hierarchy":     "Size > Color > Position > Shape — in that order",
    "spacing":       "8px grid system. Breathing room > density. Always.",
    "typography":    "2 fonts max. 1.5 line-height body. 1.2 headings.",
    "color":         "60-30-10 rule. Primary 60%, secondary 30%, accent 10%.",
    "contrast":      "WCAG AA minimum: 4.5:1 text, 3:1 large text/UI.",
    "responsiveness":"Mobile-first. Breakpoints: 640, 768, 1024, 1280.",
    "animation":     "150-300ms transitions. Ease-out for enter, ease-in for exit.",
}

_COMPONENT_PATTERNS = {
    "button":    "Label + icon optional. Min 44px touch target. Never rely on color alone.",
    "card":      "Image + title + description + CTA. Max 3 cards per row.",
    "form":      "Label above input. Error below. Never placeholder-only labels.",
    "modal":     "Title + body + actions. Always escapable. Focus trap required.",
    "nav":       "Max 7 items. Active state obvious. Mobile: hamburger or bottom nav.",
    "table":     "Sortable headers. Zebra striping optional. Sticky header on scroll.",
    "toast":     "Auto-dismiss 5s. Actionable toasts persist. Stack from bottom-right.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## UI Design: Component Specification

### Visual Hierarchy
[Layout decisions, spacing, typography choices]

### Component Specifications
[For each component: dimensions, states, interactions, responsive behavior]

### Design Tokens
[Colors, spacing, typography, shadows, borders as CSS variables]

### Accessibility Notes
[WCAG compliance, keyboard nav, screen reader considerations]

### Implementation Notes
[Tailwind classes, Framer Motion animations, responsive breakpoints]""",
        "output_keys": ["design_output", "components"],
        "max_tokens": 3000,
    },

    "ux_researcher": {
        "description": "UX research and usability specialist — user journey mapping, heuristic evaluation, usability testing plans, interaction design critique",
        "inputs": "task (str), context (str)",
        "outputs": "research_output (str), recommendations (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_HEURISTICS = {
    "visibility":       "System status always visible. User never guesses what happened.",
    "match":            "System speaks user language. No internal jargon.",
    "control":          "Undo always available. User never trapped.",
    "consistency":      "Same action = same result. Patterns reused, not reinvented.",
    "error_prevention": "Prevent errors > fix errors. Confirm destructive actions.",
    "recognition":      "Show options, don't make users remember. Autocomplete > free text.",
    "flexibility":      "Shortcuts for experts. Defaults for novices.",
    "aesthetics":       "Remove until it breaks. Every element earns its space.",
    "error_recovery":   "Plain language errors. Suggest fix. Never blame user.",
    "help":             "Contextual help > documentation. Progressive disclosure.",
}

_JOURNEY_STAGES = ["Awareness", "Consideration", "Decision", "Onboarding", "Usage", "Retention", "Advocacy"]
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## UX Research Analysis

### User Journey Map
[Stage-by-stage analysis: touchpoints, emotions, pain points, opportunities]

### Heuristic Evaluation
[Score each of Nielsen's 10 heuristics 1-5, with specific findings]

### Usability Issues (Priority Ranked)
[Severity 1-4, issue description, affected user segment, recommended fix]

### Recommendations
[Specific, actionable improvements with expected impact]

### Research Plan
[What to test next, methodology, metrics to track]""",
        "output_keys": ["research_output", "recommendations"],
        "max_tokens": 3000,
    },

    "senior_developer": {
        "description": "Senior full-stack development specialist — code architecture, code review, implementation planning, technical debt analysis, refactoring strategy",
        "inputs": "task (str), context (str)",
        "outputs": "dev_output (str), implementation_plan (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_CODE_PRINCIPLES = {
    "solid":         "Single Responsibility, Open/Closed, Liskov, Interface Seg, Dependency Inv",
    "dry":           "Don't Repeat Yourself - but don't over-abstract prematurely",
    "kiss":          "Keep It Simple. Clever code is technical debt.",
    "yagni":         "You Aren't Gonna Need It. Build what's needed now.",
    "testing":       "Test behavior not implementation. Edge cases first.",
    "naming":        "Long descriptive names > short cryptic ones. Code reads 10x more than writes.",
    "error_handling":"Fail fast, fail loud. Never swallow exceptions silently.",
    "perf":          "Measure first, optimise second. Premature optimisation is evil.",
}

_TECH_STACK = {
    "frontend":   "Next.js 15+, React 19, TypeScript strict, Tailwind v4",
    "backend":    "Python 3.12+, FastAPI, Supabase, PostgreSQL",
    "testing":    "Pytest, Playwright, Vitest, React Testing Library",
    "deployment": "Vercel (frontend), GCP VM (backend), GitHub Actions CI/CD",
    "tooling":    "ESLint, Prettier, Ruff, MyPy, pre-commit hooks",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Development Analysis

### Architecture Assessment
[Current state, patterns used, technical debt identified]

### Implementation Plan
[Step-by-step with file paths, functions, dependencies]

### Code Review
[Issues found, severity, suggested fixes with code snippets]

### Testing Strategy
[Unit tests, integration tests, edge cases to cover]

### Performance & Security
[Bottlenecks, vulnerabilities, optimization opportunities]""",
        "output_keys": ["dev_output", "implementation_plan"],
        "max_tokens": 3000,
    },

    "system_architect": {
        "description": "System architecture specialist — infrastructure design, scalability planning, service decomposition, technology selection, capacity planning",
        "inputs": "task (str), context (str)",
        "outputs": "architecture_output (str), diagram_description (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_ARCHITECTURE_PATTERNS = {
    "monolith":     "Start here. Split when you have clear bounded contexts and team boundaries.",
    "microservices":"One service per bounded context. Own DB. Async communication preferred.",
    "event_driven": "Events for decoupling. CQRS for read/write separation. Idempotency required.",
    "serverless":   "For spiky workloads. Cold starts matter. Keep functions <15s.",
    "edge":         "CDN + edge functions for latency-critical paths. Cache aggressively.",
}

_SCALABILITY_CHECKLIST = [
    "Horizontal scaling: can you add more instances?",
    "Database: read replicas, connection pooling, query optimization",
    "Caching: Redis/CDN for hot paths, cache invalidation strategy",
    "Async: queue heavy work, don't block request threads",
    "Rate limiting: protect upstream services and APIs",
    "Circuit breakers: fail gracefully when dependencies die",
    "Observability: metrics, logs, traces for every service",
]
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## System Architecture

### Current State Assessment
[What exists, what works, what doesn't scale]

### Proposed Architecture
[Components, services, data flow, technology choices with rationale]

### Scalability Plan
[Horizontal scaling, caching, async processing, database strategy]

### Risk Analysis
[Single points of failure, blast radius, mitigation strategies]

### Migration Path
[Phase 1-3 with clear milestones and rollback plans]""",
        "output_keys": ["architecture_output", "diagram_description"],
        "max_tokens": 3000,
    },

    "investment_analyst": {
        "description": "Investment analysis specialist — market research, financial modeling, risk assessment, portfolio strategy, due diligence",
        "inputs": "task (str), context (str)",
        "outputs": "analysis_output (str), recommendation (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_ANALYSIS_FRAMEWORKS = {
    "fundamental":  "Revenue, margins, growth rate, TAM, competitive moat, management quality",
    "technical":    "Price action, volume, support/resistance, momentum indicators",
    "dcf":          "Discount future cash flows. Terminal value. WACC sensitivity.",
    "comps":        "Compare multiples: P/E, EV/EBITDA, P/S against peer group",
    "risk":         "Volatility, drawdown, Sharpe ratio, correlation, tail risk",
}

_DUE_DILIGENCE_CHECKLIST = [
    "Market size and growth trajectory",
    "Competitive landscape and defensibility",
    "Revenue model sustainability",
    "Unit economics (CAC, LTV, payback period)",
    "Team capability and track record",
    "Regulatory and legal risks",
    "Technology moat or switching costs",
]
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Investment Analysis

### Market Overview
[Market size, growth drivers, competitive landscape]

### Financial Analysis
[Key metrics, valuation, growth projections]

### Risk Assessment
[Key risks ranked by probability and impact]

### Recommendation
[Buy/Hold/Sell with price target and thesis]

### Monitoring Triggers
[What would change the thesis — bull and bear scenarios]""",
        "output_keys": ["analysis_output", "recommendation"],
    },

    "recruitment_specialist": {
        "description": "Recruitment and talent acquisition specialist — job descriptions, candidate evaluation, interview design, hiring strategy",
        "inputs": "task (str), context (str)",
        "outputs": "recruitment_output (str), evaluation_criteria (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_HIRING_PRINCIPLES = {
    "bar":           "Hire for trajectory, not just current skill. Culture add > culture fit.",
    "jd_writing":    "Outcomes > requirements. Show the mission, not just the checklist.",
    "evaluation":    "Structured interviews. Same questions. Rubric scoring. Reduce bias.",
    "pipeline":      "Source → Screen → Interview → Offer → Close. Measure conversion at each.",
    "speed":         "Time-to-hire is a competitive weapon. 48hr feedback. 1-week decision.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Recruitment Strategy

### Role Definition
[Title, level, reporting structure, key outcomes expected]

### Job Description
[Mission, responsibilities, requirements, nice-to-haves, compensation range]

### Evaluation Rubric
[Scoring criteria for each interview stage]

### Interview Design
[Questions, exercises, take-home (if any), timeline]

### Sourcing Strategy
[Channels, outreach templates, referral incentives]""",
        "output_keys": ["recruitment_output", "evaluation_criteria"],
    },

    "sales_intelligence": {
        "description": "Sales intelligence and pipeline specialist — prospect research, outreach strategy, objection handling, deal qualification, pipeline analysis",
        "inputs": "task (str), context (str)",
        "outputs": "intel_output (str), action_plan (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_SALES_FRAMEWORKS = {
    "bant":       "Budget, Authority, Need, Timeline — qualify before pitching",
    "meddic":     "Metrics, Economic buyer, Decision criteria/process, Identify pain, Champion",
    "spin":       "Situation, Problem, Implication, Need-payoff — discovery sequence",
    "challenger": "Teach, Tailor, Take Control — lead with insight, not questions",
    "sandler":    "Pain → Budget → Decision — upfront contracts, no free consulting",
}

_OBJECTION_PATTERNS = {
    "price":     "Reframe to ROI. Cost of inaction > cost of solution.",
    "timing":    "What changes in 6 months? Usually nothing. Cost of delay = X.",
    "competitor":"Don't trash-talk. Ask what criteria matter most. Win on YOUR strengths.",
    "authority": "Coach the champion. Give them the internal pitch deck.",
    "need":      "Go back to discovery. If no pain, no sale. Walk away.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Sales Intelligence Report

### Prospect Profile
[Company, decision makers, budget signals, pain indicators]

### Qualification Assessment
[BANT/MEDDIC scoring with evidence]

### Outreach Strategy
[Multi-channel sequence: email, LinkedIn, phone, timing]

### Objection Preparation
[Likely objections and response frameworks]

### Action Plan
[Next 5 specific actions with owners and deadlines]""",
        "output_keys": ["intel_output", "action_plan"],
    },

    "legal_analyst": {
        "description": "Legal analysis specialist — contract review, compliance assessment, risk identification, regulatory research, IP strategy",
        "inputs": "task (str), context (str)",
        "outputs": "legal_output (str), risk_flags (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_LEGAL_AREAS = {
    "contract":    "Terms, obligations, liability caps, termination, IP assignment, non-compete",
    "compliance":  "GDPR, CCPA, SOC2, PCI-DSS, industry-specific regulations",
    "ip":          "Patents, trademarks, copyrights, trade secrets, licensing",
    "employment":  "At-will, non-compete, equity, classification, termination risk",
    "corporate":   "Formation, governance, cap table, shareholder agreements",
}

_RISK_SEVERITY = {
    "critical": "Immediate legal exposure. Stop and fix before proceeding.",
    "high":     "Significant risk. Address within 30 days.",
    "medium":   "Manageable risk. Schedule for next review cycle.",
    "low":      "Minor concern. Note and monitor.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Legal Analysis

### Summary
[Plain-English summary of the legal situation]

### Key Findings
[Clause-by-clause or issue-by-issue analysis]

### Risk Assessment
[Each risk: severity, probability, impact, mitigation]

### Recommendations
[Specific actions: clauses to negotiate, compliance steps, filings needed]

### Disclaimer
[This is AI-assisted analysis, not legal advice. Consult qualified counsel.]""",
        "output_keys": ["legal_output", "risk_flags"],
    },

    "due_diligence_analyst": {
        "description": "Due diligence specialist — company evaluation, market validation, risk scoring, investment memo preparation",
        "inputs": "task (str), context (str)",
        "outputs": "dd_output (str), risk_score (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_DD_FRAMEWORK = {
    "market":     "TAM/SAM/SOM, growth rate, market timing, secular trends",
    "product":    "Product-market fit evidence, retention, NPS, usage metrics",
    "team":       "Founder experience, key hires, board composition, advisor quality",
    "financials": "Revenue, burn rate, runway, unit economics, path to profitability",
    "legal":      "Cap table, IP ownership, litigation, regulatory exposure",
    "technology": "Tech stack, scalability, security posture, technical debt",
    "competition":"Direct/indirect competitors, differentiation, switching costs",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Due Diligence Report

### Executive Summary
[One-paragraph verdict with confidence level]

### Market Analysis
[TAM, growth, timing, competitive dynamics]

### Product & Technology
[Product-market fit evidence, tech assessment]

### Team Assessment
[Founders, key hires, gaps, culture signals]

### Financial Analysis
[Revenue, burn, unit economics, projections]

### Risk Matrix
[Each risk scored: probability (1-5) x impact (1-5)]

### Recommendation
[Invest/Pass with conditions and key milestones]""",
        "output_keys": ["dd_output", "risk_score"],
    },

    "deep_researcher": {
        "description": "Deep research specialist — multi-source synthesis, academic rigor, structured argumentation, evidence grading, comprehensive literature review",
        "inputs": "task (str), context (str)",
        "outputs": "research_output (str), sources (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_RESEARCH_METHODS = {
    "systematic":   "Define question → search strategy → inclusion criteria → synthesis",
    "comparative":  "Multiple sources → cross-reference → consensus + divergence",
    "adversarial":  "Steel-man opposing views. Attack your own thesis first.",
    "temporal":     "Track how understanding evolved. Latest ≠ best.",
    "quantitative": "Numbers > opinions. Primary data > secondary. Sample size matters.",
}

_EVIDENCE_GRADES = {
    "A": "Multiple high-quality sources agree. Strong confidence.",
    "B": "Good evidence with minor gaps. Moderate confidence.",
    "C": "Limited or conflicting evidence. Low confidence.",
    "D": "Single source or speculation. Very low confidence.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Deep Research Report

### Research Question
[Precisely stated question with scope boundaries]

### Methodology
[Sources consulted, search strategy, inclusion/exclusion criteria]

### Findings
[Evidence-graded findings (A/B/C/D) with citations]

### Analysis
[Synthesis, patterns, contradictions, knowledge gaps]

### Conclusions
[Answering the research question with confidence level]

### Limitations
[What this research cannot answer and why]""",
        "output_keys": ["research_output", "sources"],
        "max_tokens": 4000,
    },

    "product_launch_strategist": {
        "description": "Product launch strategy specialist — go-to-market planning, launch sequencing, demand generation, pricing strategy, channel selection",
        "inputs": "task (str), context (str)",
        "outputs": "launch_output (str), timeline (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_LAUNCH_PHASES = {
    "pre_launch":  "Build waitlist, seed content, establish positioning, beta testing",
    "soft_launch": "Limited release, gather feedback, fix critical issues, case studies",
    "hard_launch": "Full PR push, paid acquisition, partnerships, events",
    "post_launch": "Retention focus, upsell, community building, iterate on feedback",
}

_PRICING_MODELS = {
    "freemium":      "Free tier for adoption, paid for value. Works for PLG.",
    "subscription":  "Monthly/annual. Annual discount 15-20%. Reduce churn.",
    "usage_based":   "Pay per use. Aligns cost with value. Complex to predict.",
    "tiered":        "Good/Better/Best. Anchor on middle tier. 3 tiers max.",
    "enterprise":    "Custom pricing. Annual contracts. POC required.",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Product Launch Strategy

### Positioning
[Who it's for, what problem it solves, why now, why us]

### Launch Timeline
[Week-by-week plan across pre-launch, soft, hard, post-launch]

### Channel Strategy
[Primary and secondary channels with expected CAC and volume]

### Pricing Recommendation
[Model, tiers, anchoring strategy, competitive comparison]

### Success Metrics
[KPIs for each phase with targets and measurement plan]

### Risk Mitigation
[What could go wrong and contingency plans]""",
        "output_keys": ["launch_output", "timeline"],
    },

    "financial_planner": {
        "description": "Financial planning and analysis specialist — budgeting, forecasting, cash flow modeling, scenario planning, cost optimization",
        "inputs": "task (str), context (str)",
        "outputs": "financial_output (str), projections (str)",
        "valid_sets": "",
        "domain_knowledge": textwrap.dedent('''
_FINANCIAL_MODELS = {
    "dcf":         "Discount future cash flows. Use conservative growth rates.",
    "three_stmt":  "Income Statement → Balance Sheet → Cash Flow. Always connected.",
    "unit_econ":   "CAC, LTV, payback period, gross margin per unit.",
    "scenario":    "Base, bull, bear. Assign probabilities. Expected value = weighted avg.",
    "sensitivity": "One variable at a time. Show which inputs matter most.",
}

_COST_CATEGORIES = {
    "fixed":     "Rent, salaries, subscriptions — doesn't change with volume",
    "variable":  "COGS, API costs, commissions — scales with revenue",
    "semi_var":  "Hosting, support staff — step function, not linear",
    "one_time":  "Setup costs, migrations, legal — budget separately",
}
''').strip(),
        "prompt_body": """OUTPUT FORMAT:
## Financial Analysis

### Current State
[Revenue, costs, margins, burn rate, runway]

### Projections (12 months)
[Monthly: revenue, costs, cash flow, key assumptions]

### Scenario Analysis
[Base/Bull/Bear with probability weights]

### Cost Optimization
[Top 5 cost reduction opportunities with estimated savings]

### Recommendations
[Specific actions with financial impact and timeline]""",
        "output_keys": ["financial_output", "projections"],
    },
}


# ── Generate + Write Files ─────────────────────────────────────────────────────
def main():
    os.makedirs(AGENTS_DIR, exist_ok=True)
    created = []
    for role, spec in AGENTS.items():
        code = make_agent(
            role=role,
            description=spec["description"],
            inputs=spec["inputs"],
            outputs=spec["outputs"],
            valid_sets=spec.get("valid_sets", ""),
            domain_knowledge=spec.get("domain_knowledge", ""),
            prompt_body=spec["prompt_body"],
            output_keys=spec["output_keys"],
            max_tokens=spec.get("max_tokens", 2400),
        )
        path = os.path.join(AGENTS_DIR, f"{role}.py")
        with open(path, "w") as f:
            f.write(code)
        created.append(role)
        print(f"  ✅ Created {role}.py")

    # Register in __init__.py
    init_path = os.path.join(AGENTS_DIR, "__init__.py")
    existing = ""
    if os.path.exists(init_path):
        with open(init_path) as f:
            existing = f.read()

    new_imports = []
    new_registry = []
    for role in created:
        import_line = f"from agents.{role} import {role}_node"
        registry_line = f'    "{role}": {role}_node,'
        if import_line not in existing:
            new_imports.append(import_line)
            new_registry.append(registry_line)

    if new_imports:
        # Append to existing
        with open(init_path, "a") as f:
            f.write("\n# ── New agents (awesome-llm-apps inspired) ──\n")
            for line in new_imports:
                f.write(line + "\n")
        print(f"\n  📝 Updated __init__.py with {len(new_imports)} new imports")

    # Also update AGENT_REGISTRY if it exists
    registry_path = os.path.join(AGENTS_DIR, "..", "AGENT_REGISTRY.md")
    if os.path.exists(registry_path):
        with open(registry_path, "a") as f:
            f.write("\n\n## New Agents (awesome-llm-apps inspired)\n\n")
            f.write("| Role | Description |\n|------|-------------|\n")
            for role, spec in AGENTS.items():
                f.write(f"| `{role}` | {spec['description'][:80]}... |\n")
        print("  📝 Updated AGENT_REGISTRY.md")

    print(f"\n✅ Deployed {len(created)} new agents to {AGENTS_DIR}")
    print("   Next: restart the server and run batch tests")


if __name__ == "__main__":
    main()
