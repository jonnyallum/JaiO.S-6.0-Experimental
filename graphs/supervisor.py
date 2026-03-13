"""
Supervisor Graph — Orchestrator routing layer.

Routes tasks to specialist skill nodes based on keyword classification.
Pattern: START → route → execute_skill → END

Persona for the orchestrator is resolved via personas/config.py (role: orchestrator).
All routing logic is role-based — no persona names hardcoded.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agents.github_intelligence import GitHubIntelState, github_intelligence_node
from agents.security_audit import SecurityAuditState, security_audit_node
from agents.architecture_review import ArchitectureReviewState, architecture_review_node
from agents.data_extraction import DataExtractionState, data_extraction_node
from agents.quality_validation import QualityValidationState, quality_validation_node
from agents.brief_writer import BriefWriterState, brief_writer_node
from agents.code_reviewer import CodeReviewState, code_reviewer_node
from agents.dependency_audit import DependencyAuditState, dependency_audit_node
from agents.social_post_generator import SocialPostState, social_post_generator_node
from agents.supabase_intelligence import BrainIntelState, supabase_intelligence_node
from agents.monetisation_strategist import MonetisationState, monetisation_strategist_node
from agents.sales_conversion import SalesConversionState, sales_conversion_node
from agents.content_scaler import ContentScalerState, content_scaler_node
from agents.automation_architect import AutomationState, automation_architect_node
from agents.business_intelligence import BIReportState, business_intelligence_node
from agents.seo_specialist import SEOState, seo_specialist_node
from agents.competitor_monitor import CompetitorIntelState, competitor_monitor_node
from agents.email_architect import EmailSequenceState, email_architect_node
from agents.video_brief_writer import VideoBriefState, video_brief_writer_node
from agents.funnel_architect import FunnelState, funnel_architect_node
from agents.analytics_reporter import analytics_reporter_node, AnalyticsState
from agents.ad_copy_writer import ad_copy_writer_node, AdCopyState
from agents.launch_orchestrator import launch_orchestrator_node, LaunchState
from agents.case_study_writer import case_study_writer_node, CaseStudyState
from agents.knowledge_base_writer import knowledge_base_writer_node, KnowledgeBaseState
from agents.agent_builder import agent_builder_node, AgentBuilderState
from agents.proposal_writer import proposal_writer_node, ProposalState
from agents.product_strategist import product_strategist_node, ProductStrategyState
from agents.pricing_strategist import pricing_strategist_node, PricingState
from agents.course_designer import course_designer_node, CourseState
from agents.document_qa import DocumentQAState, document_qa_node
from agents.vision_analyst import VisionState, vision_analyst_node
from agents.chatbot_designer import chatbot_designer_node, ChatbotState
from agents.persona_builder import persona_builder_node, PersonaState
from agents.pr_writer import pr_writer_node, PRState
from agents.ab_test_designer import ab_test_designer_node, ABTestState
from agents.investor_pitch_writer import investor_pitch_writer_node, InvestorPitchState
from agents.brand_voice_guide import brand_voice_guide_node, BrandVoiceState
from agents.ecommerce_strategist import ecommerce_strategist_node, EcommerceState
from agents.data_parser import data_parser_node, DataParserState
from graphs.intent_extractor import extract_intent, AGENT_SCHEMAS
from agents.research_analyst import research_analyst_node, ResearchState
from agents.api_integration_agent import api_integration_agent_node, ApiIntegrationAgentState
from agents.betting_systems import betting_systems_node, BettingSystemsState
from agents.code_executor import code_executor_node, CodeExecutorState
from agents.cost_tracker import cost_tracker_node, CostTrackerState
from agents.darts_analyst import darts_analyst_node, DartsAnalystState
from agents.error_recovery_agent import error_recovery_agent_node, ErrorRecoveryAgentState
from agents.eval_judge import eval_judge_node, EvalJudgeState
from agents.feedback_collector import feedback_collector_node, FeedbackCollectorState
from agents.football_tactical import football_tactical_node, FootballTacticalState
from agents.formula1_analyst import formula1_analyst_node, Formula1AnalystState
from agents.horse_racing import horse_racing_node, HorseRacingState
from agents.human_gate import human_gate_node, HumanGateState
from agents.image_prompt_engineer import image_prompt_engineer_node, ImagePromptEngineerState
from agents.motogp_analyst import motogp_analyst_node, MotogpAnalystState
from agents.onboarding_agent import onboarding_agent_node, OnboardingAgentState
from agents.rag_retriever import rag_retriever_node, RagRetrieverState
from agents.risk_analyst import risk_analyst_node, RiskAnalystState
from agents.roulette_math import roulette_math_node, RouletteMathState
from agents.summariser import summariser_node, SummariserState
from agents.translator import translator_node, TranslatorState
from agents.workflow_planner import workflow_planner_node, WorkflowPlannerState
from agents.pipeline_monitor import pipeline_monitor_node, PipelineState
from agents.customer_success import customer_success_node, CustomerSuccessState
from agents.truth_verifier import truth_verifier_node, TruthVerifierState
from agents.content_auditor import content_auditor_node, ContentAuditorState
from agents.process_auditor import process_auditor_node, ProcessAuditorState
from agents.venture_ideator import venture_ideator_node, VentureIdeatorState
from agents.voice_synthesiser import voice_synthesiser_node, VoiceSynthesiserState
from agents.fullstack_architect import fullstack_architect_node, FullstackArchitectState
from agents.database_architect import database_architect_node, DatabaseArchitectState
from agents.supabase_specialist import supabase_specialist_node, SupabaseSpecialistState
from agents.devops_engineer import devops_engineer_node, DevOpsEngineerState
from agents.deployment_specialist import deployment_specialist_node, DeploymentSpecialistState
from agents.performance_auditor import performance_auditor_node, PerformanceAuditorState
from agents.mcp_builder import mcp_builder_node, McpBuilderState
from agents.gcp_ai_specialist import gcp_ai_specialist_node, GcpAiSpecialistState
from agents.ui_designer import ui_designer_node, UiDesignerState
from agents.creative_director import creative_director_node, CreativeDirectorState
from agents.copywriter import copywriter_node, CopywriterState
from agents.project_manager import project_manager_node, ProjectManagerState
from agents.financial_analyst import financial_analyst_node, FinancialAnalystState
from agents.legal_advisor import legal_advisor_node, LegalAdvisorState
from agents.fact_checker import fact_checker_node, FactCheckerState
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from agents.ux_researcher import ux_researcher_node
from agents.senior_developer import senior_developer_node
from agents.system_architect import system_architect_node
from agents.investment_analyst import investment_analyst_node
from agents.recruitment_specialist import recruitment_specialist_node
from agents.sales_intelligence import sales_intelligence_node
from agents.legal_analyst import legal_analyst_node
from agents.due_diligence_analyst import due_diligence_analyst_node
from agents.deep_researcher import deep_researcher_node
from agents.product_launch_strategist import product_launch_strategist_node
from agents.financial_planner import financial_planner_node

log = structlog.get_logger()

# ── Keyword routing table ─────────────────────────────────────────────────────────
ROUTING_RULES: dict[str, list[str]] = {
    # ── Technical ─────────────────────────────────────────────────────────────
    "github_intelligence": ["github", "repo", "repository", "git commit", "pull request", "github issue", "git branch", "contributor", "git merge", "git diff", "github actions workflow", "code diff", "open pr", "commits", "branch", "repo audit", "github repo"],
    "security_audit": ["security audit", "security review", "vulnerability", "security check", "pentest", "penetration test", "access control", "permission model", "encrypt", "auth review", "token security", "secret management", "cve", "exposure risk", "security hardening", "owasp", "security scan"],
    "architecture_review": ["review architecture", "architecture review", "architecture audit", "system design review", "review the stack", "refactor plan", "architectural decision", "design pattern review", "tech debt", "review this architecture", "architecture assessment", "scalability", "infra review", "architecture bottleneck", "scalability review", "infra design", "review our", "infrastructure design", "recommend improvements", "review our infrastructure"],
    "data_extraction": ["parse", "extract data", "json schema", "csv parse", "convert data", "transform data", "scrape", "data pipeline", "schema extract", "api response", "extract structured"],
    "quality_validation": [
        "quality assurance", "validate output", "qa review", "pass criteria", "fail criteria", "quality score", "qc check",
    ],
    "code_reviewer": ["review code", "code review", "code quality", "feedback on code", "lint", "smell", "file review", "pull request", "pr review"],
    "dependency_audit": [
        "dependency", "dependencies", "package", "requirements", "npm",
        "pip", "outdated", "licence", "license", "lockfile",
    ],
    "fullstack_architect": [
        "fullstack", "full stack", "full-stack", "next.js", "nextjs", "react",
        "frontend", "backend", "api design", "rest api", "graphql", "typescript",
        "web app", "web application", "spa",
    ],
    "database_architect": [
        "database", "db schema", "postgres", "postgresql", "mysql", "sqlite",
        "table design", "migration", "index", "query optimisation", "query optimization",
        "normalisation", "normalization", "prisma", "drizzle", "typeorm",
    ],
    "supabase_specialist": [
        "supabase schema", "supabase rls", "row level security", "supabase function",
        "supabase edge", "supabase storage", "supabase auth", "supabase realtime",
        "supabase table", "supabase policy",
    ],
    "devops_engineer": ["devops", "ci/cd", "github actions", "docker", "container", "kubernetes", "k8s", "pipeline", "deploy pipeline", "build pipeline", "infra", "infrastructure", "terraform", "ansible", "bash script", "nginx", "reverse proxy", "ci/cd pipeline", "server config"],
    "deployment_specialist": ["deploy", "deployment", "gcp deploy", "cloud run", "app engine", "vercel", "netlify", "fly.io", "railway", "server setup", "production deploy", "release", "rollout", "ship", "staging", "deploy to", "ship the", "staging env"],
    "performance_auditor": [
        "performance", "slow", "latency", "lighthouse", "core web vitals",
        "page speed", "load time", "bottleneck", "profil", "optimise speed",
        "optimize speed", "caching", "cdn",
    ],
    "mcp_builder": [
        "mcp server", "mcp tool", "build mcp", "model context protocol",
        "fastmcp", "mcp endpoint", "mcp integration", "tool server",
        "create mcp", "mcp scaffold",
    ],
    "gcp_ai_specialist": [
        "gcp", "google cloud", "vertex ai", "cloud ai", "bigquery",
        "gcp vm", "compute engine", "cloud storage", "gcs",
        "cloud function", "pub/sub", "gcp setup",
    ],
    "data_parser": ["data parsing", "raw data", "parse data", "structure data", "unstructured data", "clean data", "normalise data", "normalize data", "tabular data", "data pipeline", "etl", "csv", "json payload", "normalize", "parse file", "decode", "parse this", "csv file", "normalize the", "decode this"],
    "agent_builder": [
        "build agent", "create agent", "new agent", "agent spec",
        "agent design", "agent skill", "skill node", "langgraph agent",
        "agent file", "write agent", "build a spec for a", "spec for an agent",
        "build an agent", "design an agent", "agent blueprint",
    ],
    "pipeline_monitor": [
        "monitor pipeline", "pipeline status", "workflow status", "agent health",
        "system health", "orchestration status", "task queue", "job status",
        "pipeline alert", "pipeline report",
    ],
    "process_auditor": ["process audit", "workflow audit", "sop audit", "process review", "operational review", "efficiency audit", "process improvement", "workflow optimisation", "workflow optimization", "bottleneck audit", "onboarding process", "friction audit", "audit this process", "audit this workflow", "friction points", "manual steps", "audit the process", "friction point", "continuous improvement"],
    "truth_verifier": ["truth verif", "verify this claim", "verify the claim", "verify claim", "is this true", "truth check", "artifact verification", "13-gate", "gate verification", "output verification", "verify artifact", "cross-reference", "source check", "claim verification", "fact-check", "fact check", "verify accuracy", "truth verification", "accuracy of", "verify the", "verify the accuracy", "accuracy of this"],

    # ── Content & Creative ────────────────────────────────────────────────────
    "social_post_generator": [
        "social", "post", "facebook", "instagram", "caption",
        "broadcast", "hashtag", "fb post", "ig post",
    ],
    "content_scaler": [
        "content variant", "scale content", "content a/b", "headline variant",
        "email subject variant", "blog intro", "content repurpose",
        "content batch", "content scale",
    ],
    "ad_copy_writer": [
        "ad copy", "advertisement", "google ad", "facebook ad", "meta ad",
        "paid ad", "ppc copy", "ad headline", "ad creative copy",
        "display ad", "sponsored post copy",
    ],
    "copywriter": ["copy", "write copy", "headline", "headline copy", "headline variant", "website copy", "landing page copy", "homepage copy", "about page", "tagline", "slogan", "brand copy", "product description", "marketing copy", "write headlines", "ad copy", "conversion copy", "email copy", "write compelling", "draft the", "copy for", "draft the email", "email sequence", "product launch campaign"],
    "brand_voice_guide": [
        "brand voice", "tone of voice", "brand guidelines", "brand style",
        "writing guidelines", "communication style", "brand language",
        "brand personality", "content guidelines",
    ],
    "creative_director": ["creative direction", "creative brief", "creative strategy", "campaign concept", "creative campaign", "art direction", "creative vision", "visual concept", "creative review", "visual storytelling", "creative concept", "storytelling"],
    "video_brief_writer": [
        "video brief", "tiktok", "reels", "youtube short", "short-form video",
        "hook script", "video script", "film brief", "b-roll", "video content",
    ],
    "voice_synthesiser": [
        "voice script", "tts script", "text to speech", "elevenlabs",
        "voice over", "voiceover", "audio script", "podcast script",
        "spoken content", "narration script",
    ],
    "ui_designer": ["ui design", "user interface", "wireframe", "mockup", "figma", "component design", "ux design", "user experience", "dashboard design", "design a dashboard", "dashboard component", "design a component", "colour scheme", "color scheme", "layout design", "visual design", "design the ui", "design ui", "screen design", "page design", "pixel-perfect", "ui mockup", "design system", "visual hierarchy", "pixel perfect"],
    "content_auditor": [
        "content audit", "audit content", "audit this content", "content quality",
        "audit the content", "content gap", "content depth", "content review",
        "content performance", "fluff", "thin content", "content issues",
    ],
    "pr_writer": [
        "press release", "pr write", "media release", "news release",
        "announcement", "public relations", "journalist pitch",
        "media pitch", "press coverage",
    ],

    # ── Business & Strategy ───────────────────────────────────────────────────
    "brief_writer": [
        "brief", "scope of work", "sow", "discovery",
        "onboarding doc", "client report", "write brief",
    ],
    "proposal_writer": [
        "proposal", "write proposal", "client proposal", "business proposal",
        "rfp response", "quote document", "statement of work",
        "service proposal", "project proposal",
    ],
    "monetisation_strategist": [
        "monetis", "monetiz", "revenue", "pricing model", "mrr",
        "arr", "subscription", "upsell", "profit", "income", "earn",
    ],
    "pricing_strategist": [
        "pricing strategy", "price point", "price tier", "tiered pricing",
        "value-based pricing", "cost-plus", "competitor pricing",
        "price increase", "pricing page", "how much to charge",
    ],
    "product_strategist": ["product strategy", "product roadmap", "product vision", "feature prioritisation", "feature prioritization", "mvp", "product market fit", "user story", "product brief", "product spec", "innovation sprint", "q3", "q4", "product strat", "define the product", "based on user feedback", "strategy for q"],
    "sales_conversion": [
        "prospect", "close", "objection", "deal", "pipeline", "crm",
        "follow up", "pitch", "negotiate", "sales call", "cold",
    ],
    "funnel_architect": [
        "conversion funnel", "sales funnel", "landing page strategy", "cro",
        "objection handling", "offer structure", "upsell map", "funnel design",
        "top of funnel", "bottom of funnel", "awareness funnel",
    ],
    "ecommerce_strategist": [
        "ecommerce", "e-commerce", "shopify", "woocommerce", "online store",
        "product listing", "basket", "checkout", "cart abandonment",
        "product page", "shop strategy",
    ],
    "launch_orchestrator": ["launch", "go to market", "gtm", "product launch", "campaign launch", "release plan", "launch plan", "launch strategy", "ship", "launch checklist", "launch timeline", "launch day", "go-to-market", "launch sequence", "launch orchestrat", "orchestrate", "all channels"],
    "venture_ideator": [
        "startup idea", "business idea", "venture idea", "new business",
        "side project", "opportunity", "market gap", "business concept",
        "idea validation", "new venture",
    ],
    "investor_pitch_writer": [
        "investor pitch", "pitch deck", "fundraising", "seed round",
        "series a", "angel investor", "vc pitch", "investment memo",
        "pitch narrative", "funding deck",
    ],

    # ── Intelligence & Research ───────────────────────────────────────────────
    "business_intelligence": ["kpi", "metric", "dashboard", "bi report", "analytics report", "forecast", "trend", "performance report", "revenue report", "data analysis", "business report", "kpi dashboard", "reporting framework", "executive report", "business review", "reporting", "executive", "decision-making", "executive decision", "build a reporting"],
    "analytics_reporter": [
        "analytics", "traffic data", "conversion data", "engagement data",
        "retention data", "google analytics", "raw metrics", "metric report",
        "data report", "weekly report", "monthly report",
    ],
    "research_analyst": ["research", "market research", "competitive analysis", "due diligence", "academic", "trend analysis", "fact finding", "deep research", "investigate", "background research", "market trends", "competitive landscape", "deep dive", "market analysis"],
    "competitor_monitor": [
        "competitor url", "scrape competitor", "analyze competitor site",
        "monitor competitor", "competitor website", "scrape their site",
        "analyze their pricing page", "competitor landing page",
        "intel on competitor", "investigate competitor",
    ],
    "seo_specialist": [
        "seo", "search engine", "on-page", "meta tag", "schema markup",
        "keyword gap", "keyword rank", "serp", "google ranking", "canonical",
    ],
    "supabase_intelligence": [
        "supabase brain", "shared brain", "agent data", "learnings",
        "chatroom", "who is agent", "which agent", "agent roster",
        "orchestra status", "brain query",
    ],

    # ── Operations & Delivery ─────────────────────────────────────────────────
    "automation_architect": [
        "automat", "n8n", "workflow trigger", "webhook", "cron",
        "zapier", "make.com", "integration build", "email sequence automation",
        "automate task",
    ],
    "email_architect": ["email sequence", "email campaign", "nurture sequence", "drip", "follow-up email", "onboarding email", "re-engagement", "cold email", "email series", "write emails", "email template", "resend", "email notification", "email notif", "email automat", "welcome email", "notifications with n8n", "automated email", "email with"],
    "project_manager": ["project plan", "project timeline", "project milestone", "task breakdown", "project phases", "project scope", "resource plan", "project brief", "sprint planning", "kanban board", "gantt", "delivery plan", "create a project plan", "build a project plan", "project schedule", "milestones", "coordinate", "timeline", "project management"],
    "customer_success": ["customer success", "client health", "churn", "retention strategy", "onboarding client", "client check-in", "nps", "client feedback", "customer satisfaction", "account management", "support ticket", "customer support", "billing issue", "client success", "beta users", "customer issue", "feedback loop"],
    "knowledge_base_writer": [
        "knowledge base", "help doc", "faq", "documentation", "how-to guide",
        "support article", "user guide", "wiki", "write docs", "internal doc",
    ],
    "case_study_writer": [
        "case study", "success story", "client win", "customer story",
        "results story", "write case study", "testimonial narrative",
        "before and after", "client results",
    ],
    "course_designer": [
        "course", "curriculum", "lesson plan", "learning outcome",
        "e-learning", "online course", "module design", "course outline",
        "training programme", "course content",
    ],
    "chatbot_designer": [
        "chatbot", "chat flow", "conversation design", "bot flow",
        "whatsapp bot", "messenger bot", "ai chat", "chat script",
        "dialogue design", "bot script",
    ],
    "persona_builder": [
        "persona", "buyer persona", "customer avatar", "ideal client profile",
        "icp", "target audience", "audience profile", "user persona",
        "customer profile", "demographic profile",
    ],
    "financial_analyst": [
        "financial", "cashflow", "cash flow", "p&l", "profit and loss",
        "balance sheet", "financial model", "revenue projection",
        "financial forecast", "unit economics", "burn rate",
    ],
    "legal_advisor": [
        "legal", "contract", "terms and conditions", "gdpr", "compliance",
        "privacy policy", "nda", "intellectual property", "ip",
        "trademark", "legal risk", "data protection",
    ],
    "fact_checker": [
        "fact check", "fact-check", "fact checking", "check the fact", "is this accurate",
        "check this claim", "source this", "citation needed", "is it true",
        "evidence for", "back this up",
    ],
    "ab_test_designer": [
        "a/b test", "ab test", "split test", "experiment design",
        "hypothesis test", "variant test", "conversion test",
        "test design", "a/b variant", "multivariate",
    ],
    # ── Batch 6: awesome-llm-apps inspired ────────────────────────────────
    "ux_researcher": [
        "ux research", "user research", "usability", "user testing",
        "journey map", "user journey", "heuristic evaluation", "persona research",
        "user interview", "ux audit", "user experience",
    ],
    "senior_developer": [
        "senior dev", "code architecture", "tech debt", "refactor code",
        "code implementation", "write code", "implement feature", "coding",
        "debug code", "fix bug", "software development",
    ],
    "system_architect": ["system architecture", "infrastructure design", "microservices", "service mesh", "load balancing", "scalability", "system design", "distributed system", "high availability", "fault tolerance", "microservice", "platform design", "distributed", "design a", "real-time", "data pipeline", "best system", "best architecture"],
    "investment_analyst": [
        "investment analysis", "stock analysis", "portfolio", "market research",
        "financial modeling", "valuation", "equity research", "investment thesis",
        "asset allocation", "risk assessment investment",
    ],
    "recruitment_specialist": [
        "recruitment", "hiring", "talent acquisition", "job description",
        "interview", "candidate", "onboarding", "headhunt", "staffing",
        "job posting", "recruit",
    ],
    "sales_intelligence": [
        "sales intelligence", "prospect research", "lead scoring",
        "sales pipeline", "outreach strategy", "prospect analysis",
        "sales enablement", "competitive selling", "account research",
    ],
    "legal_analyst": [
        "legal analysis", "contract review", "legal risk", "compliance review",
        "regulatory", "legal due diligence", "terms of service", "privacy policy",
        "legal opinion", "contract clause",
    ],
    "due_diligence_analyst": ["due diligence", "company evaluation", "market validation", "risk scoring", "business assessment", "company research", "acquisition analysis", "dd report", "target evaluation", "acquisition target", "company analysis", "due-diligence", "acquisition", "financial health", "before partnership"],
    "deep_researcher": ["deep research", "literature review", "academic research", "systematic review", "evidence synthesis", "research paper", "meta-analysis", "comprehensive research", "in-depth research", "thorough research", "research report", "scholarly", "academic", "thorough", "quantum", "emerging technology"],
    "product_launch_strategist": ["product launch", "go to market", "gtm strategy", "launch plan", "launch sequence", "product release", "market entry", "launch checklist", "product rollout", "product launch strategy", "launch strategy", "pre-launch", "post-launch", "launch strateg"],
    "financial_planner": ["financial plan", "budget", "forecast", "cash flow", "financial projection", "expense tracking", "revenue forecast", "financial model", "break even", "profit margin", "financial planning", "financial forecast", "budget plan", "budget allocation", "budget alloc", "12 months"],
    "horse_racing": ["horse", "racing", "cheltenham", "ascot", "jockey", "handicap", "form guide", "going"],
    "football_tactical": ["football", "premier league", "tactical", "match analysis", "xg", "lineup"],
    "formula1_analyst": ["formula 1", "f1", "grand prix", "pitstop", "qualifying", "grid"],
    "darts_analyst": ["darts", "pdc", "checkout", "180", "nine darter", "averages"],
    "motogp_analyst": ["motogp", "moto gp", "telemetry", "rossi", "marquez"],
    "betting_systems": ["betting", "odds", "bookmaker", "accumulator", "value bet", "stake", "multi-market", "betting coordination", "sports market", "betting system"],
    "roulette_math": ["roulette", "casino", "probability", "house edge", "martingale"],

    # ── awesome-llm-apps + new capability agents (Loop 2) ──
    "eval_judge": ["evaluate", "judge", "score output", "quality check", "grade", "rating", "rubric", "evaluate quality", "judge output", "quality rubric", "grade response", "judge whether"],
    "code_executor": ["execute code", "run code", "validate code", "debug code", "code review", "sandbox"],
    "rag_retriever": ["retrieve", "vector search", "knowledge base", "semantic search", "rag", "embeddings"],
    "human_gate": ["approval", "human review", "sign off", "manual check", "gate", "checkpoint"],
    "workflow_planner": ["plan workflow", "decompose task", "task planning", "step by step plan", "workflow design", "workflow plan", "task sequence", "workflow map", "process flow", "map out", "production pipeline"],
    "summariser": ["summarise", "summarize", "summary", "tldr", "condense", "digest", "recap", "key findings", "summarize the", "summarise the"],
    "translator": ["translate", "translation", "language", "multilingual", "localise", "localize", "spanish", "french", "german", "translate this", "into spanish", "into french", "into german", "convert our product"],
    "image_prompt_engineer": ["image prompt", "dall-e", "midjourney", "stable diffusion", "image generation", "visual prompt"],
    "api_integration_agent": ["api integration", "rest api", "graphql", "webhook design", "api design", "endpoint"],
    "risk_analyst": ["risk assessment", "risk analysis", "risk score", "risk matrix", "mitigation", "risk register", "vendor risk", "risk associat", "vendor partner", "risk matri", "score the risk", "risks associated", "partnership risk"],
    "onboarding_agent": ["onboard", "onboarding", "new client", "client setup", "welcome pack", "kickoff"],
    "feedback_collector": ["feedback", "nps", "csat", "survey", "user feedback", "satisfaction"],
    "cost_tracker": ["cost tracking", "token usage", "api costs", "budget", "spend", "cost optimisation"],
    "error_recovery_agent": ["error recovery", "diagnose error", "stack trace", "error handling", "debug failure", "incident", "postmortem", "production error", "recover error", "diagnose this"],
    "document_qa": ["document qa", "rag", "document analysis", "search documents", "answer from docs", "knowledge base query", "document question", "file analysis", "pdf analysis", "text search", "answer from document", "internal documentation", "query document", "from our", "document search", "internal doc", "search our knowledge", "answer this question from", "from our internal"],
    "vision_analyst": ["image analysis", "vision", "screenshot analysis", "visual audit", "image qa", "describe image", "extract text from image", "ocr", "ui audit", "visual review", "image", "screenshot"],
}

# ── Pipeline templates: multi-agent sequences for common workflows ──
PIPELINE_TEMPLATES = {
    "technical_audit": ["security_audit", "architecture_review", "code_reviewer", "quality_validation"],
    "client_onboarding": ["research_analyst", "proposal_writer", "project_manager"],
    "product_launch": ["product_strategist", "launch_orchestrator", "copywriter", "social_post_generator"],
    "seo_campaign": ["research_analyst", "seo_specialist", "copywriter", "content_scaler"],
    "investor_ready": ["research_analyst", "financial_analyst", "investor_pitch_writer"],
    "ecommerce_launch": ["ecommerce_strategist", "copywriter", "seo_specialist", "email_architect"],
    "brand_refresh": ["research_analyst", "brand_voice_guide", "copywriter", "creative_director"],
    "legal_review": ["legal_advisor", "fact_checker", "truth_verifier"],
    "devops_pipeline": ["fullstack_architect", "devops_engineer", "deployment_specialist", "security_audit"],
    "data_deep_dive": ["data_extraction", "data_parser", "business_intelligence", "analytics_reporter"],
    "course_creation": ["research_analyst", "course_designer", "copywriter", "video_brief_writer"],
    "sales_blitz": ["research_analyst", "sales_conversion", "email_architect", "ad_copy_writer"],
    "automation_setup": ["automation_architect", "fullstack_architect", "deployment_specialist"],
    "venture_exploration": ["venture_ideator", "research_analyst", "monetisation_strategist", "pricing_strategist"],
    "competitor_intel": ["competitor_monitor", "research_analyst", "business_intelligence"],

    # ── Loop 2 pipelines (awesome-llm-apps patterns) ──
    "quality_assured_content": ["research_analyst", "copywriter", "eval_judge"],
    "risk_assessment": ["research_analyst", "risk_analyst", "summariser"],
    "client_onboarding": ["onboarding_agent", "project_manager", "proposal_writer"],
    "error_diagnosis": ["error_recovery_agent", "code_executor", "summariser"],
    "multilingual_content": ["copywriter", "translator", "eval_judge"],
    "visual_content_brief": ["creative_director", "image_prompt_engineer", "copywriter"],
    "api_design": ["system_architect", "api_integration_agent", "code_executor"],
    "betting_full_card": ["betting_systems", "football_tactical", "horse_racing", "summariser"],
    "f1_race_preview": ["formula1_analyst", "research_analyst", "summariser"],
    "task_orchestration": ["workflow_planner", "project_manager", "summariser"],

}





# ── Multi-Agent Pipeline Routes ──────────────────────────────────────────────
PIPELINE_ROUTES: dict[str, list[str]] = {
    "content_campaign": ["research_analyst", "copywriter", "social_post_generator"],
    "blog_and_social": ["copywriter", "social_post_generator"],
    "product_launch_full": ["research_analyst", "product_strategist", "launch_orchestrator", "social_post_generator"],
    "security_review_full": ["security_audit", "code_reviewer", "architecture_review"],
    "client_proposal_full": ["research_analyst", "proposal_writer", "pricing_strategist"],
    "brand_content_pack": ["brand_voice_guide", "copywriter", "social_post_generator", "email_architect"],
    "competitive_launch": ["competitor_monitor", "product_strategist", "launch_orchestrator"],
    "due_diligence_full": ["research_analyst", "financial_analyst", "legal_advisor", "due_diligence_analyst"],
}

PIPELINE_KEYWORDS: dict[str, list[str]] = {
    "content_campaign": ["blog post and social", "blog and twitter", "blog and linkedin", "content campaign", "blog post and tweet", "write a blog", "blog plus social"],
    "blog_and_social": ["blog and social media", "blog post with tweets", "article and social"],
    "product_launch_full": ["full launch plan", "go-to-market with research", "launch with competitive analysis"],
    "security_review_full": ["full security review", "comprehensive security audit", "security and code review"],
    "client_proposal_full": ["client proposal with pricing", "full proposal", "proposal and pricing"],
    "brand_content_pack": ["brand content package", "full brand content", "brand voice and content"],
    "competitive_launch": ["competitive launch", "launch with competitor analysis"],
    "due_diligence_full": ["full due diligence", "comprehensive due diligence", "investment due diligence"],
}


def detect_pipeline(task: str) -> Optional[list[str]]:
    """Check if task requires multi-agent pipeline execution."""
    task_lower = task.lower()
    for pipeline_name, keywords in PIPELINE_KEYWORDS.items():
        for kw in keywords:
            if kw in task_lower:
                pipeline = PIPELINE_ROUTES[pipeline_name]
                log.info("pipeline.detected", pipeline=pipeline_name, agents=pipeline)
                return pipeline
    return None

class SupervisorState(BaseState):
    task: str
    repo_owner: Optional[str]
    repo_name: Optional[str]
    selected_role: str
    result: str
    pipeline: Optional[list[str]]


def _classify_task_keywords(task: str) -> tuple[str, int]:
    """Fast keyword-based classification. Returns (role, score)."""
    task_lower = task.lower()
    scores = {role: 0 for role in ROUTING_RULES}
    for role, keywords in ROUTING_RULES.items():
        for kw in keywords:
            if kw in task_lower:
                scores[role] += 1
    best = max(scores, key=lambda r: scores[r])
    return (best, scores[best]) if scores[best] > 0 else ("general_assistant", 0)


def _classify_task_llm(task: str) -> tuple[str, float]:
    """Use Claude Haiku to classify task with confidence score."""
    try:
        from anthropic import Anthropic
        client = Anthropic()

        # Build compact role list (name: top 3 keywords)
        role_summary = "\n".join(
            f"  {role}: {', '.join(kws[:4])}"
            for role, kws in ROUTING_RULES.items()
        )

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            system="You are a task router for an AI agency. Given a task, select the single best agent role. Respond ONLY with valid JSON: {\"role\": \"role_name\", \"confidence\": 0.0-1.0}",
            messages=[{
                "role": "user",
                "content": f"Task: {task}\n\nAvailable roles:\n{role_summary}\n\nWhich role handles this best?"
            }],

        )
        import json
        text = response.content[0].text.strip()
        # Handle potential markdown wrapping
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        role = parsed.get("role", "general_assistant")
        conf = float(parsed.get("confidence", 0.5))
        # Validate role exists
        if role in ROUTING_RULES:
            return (role, conf)
        # Fuzzy match — check if LLM returned a close name
        for r in ROUTING_RULES:
            if r in role or role in r:
                return (r, conf * 0.9)
        return ("general_assistant", 0.3)
    except Exception as e:
        log.warning("llm_router.failed", error=str(e))
        return ("general_assistant", 0.0)


def _classify_task(task: str) -> str:
    """
    Hybrid router: try LLM first, fall back to keywords.
    Strategy:
      1. Run keyword matcher (instant, free)
      2. If keyword confidence >= 2 matches, use it (clear signal)
      3. Otherwise, run LLM router (Haiku, ~$0.0001/call)
      4. If LLM confidence >= 0.6, use LLM result
      5. Otherwise, use keyword result or default to general_assistant
    """
    # Step 1: Fast keyword pass
    kw_role, kw_score = _classify_task_keywords(task)

    # Step 2: High keyword confidence = skip LLM
    if kw_score >= 2:
        log.info("router.keyword_match", role=kw_role, score=kw_score, method="keyword")
        return kw_role

    # Step 3: LLM classification
    llm_role, llm_conf = _classify_task_llm(task)
    log.info("router.llm_result", role=llm_role, confidence=llm_conf,
             kw_role=kw_role, kw_score=kw_score, method="hybrid")

    # Step 4: Use LLM if confident
    if llm_conf >= 0.6:
        return llm_role

    # Step 5: Fallback chain
    if kw_score >= 1:
        return kw_role

    return llm_role if llm_conf > 0 else "general_assistant"


def route_node(state: SupervisorState) -> dict:
    """Classify task -> single role OR multi-agent pipeline."""
    task = state.get("task", "") or ""
    # 1. Check for multi-agent pipeline FIRST
    pipeline = detect_pipeline(task)
    if pipeline:
        log.info("route.pipeline", task=task[:80], pipeline=pipeline)
        return {"selected_role": "pipeline:" + ",".join(pipeline), "pipeline": pipeline}
    # 2. Fall back to existing _classify_task routing
    selected = _classify_task(task)
    log.info("supervisor.routing", selected=selected, task_preview=task[:80])
    return {"selected_role": selected, "pipeline": None}


def execute_single_agent(state: SupervisorState) -> dict:  # noqa: C901
    """Dispatch to the selected skill node."""
    role        = state["selected_role"]
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    repo_owner  = state.get("repo_owner") or "jonnyallum"
    repo_name   = state.get("repo_name")  or "JaiO.S-6.0-Experimental"
    task        = state["task"]
    base        = {
        "workflow_id": workflow_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "error":       None,
    }

    log.info("supervisor.executing", role=role, workflow_id=workflow_id)

    # ── Technical agents ──────────────────────────────────────────────────────

    if role == "github_intelligence":
        r = github_intelligence_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "query": task, "intelligence": "",
        })
        return {"result": r.get("intelligence", ""), "error": r.get("error")}

    elif role == "security_audit":
        r = security_audit_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "security_report": "", "risk_level": "UNKNOWN",
        })
        return {"result": r.get("security_report", ""), "error": r.get("error")}

    elif role == "architecture_review":
        r = architecture_review_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": "general", "architecture_report": "",
        })
        return {"result": r.get("architecture_report", ""), "error": r.get("error")}

    elif role == "data_extraction":
        r = data_extraction_node({
            **base, "agent": role,
            "raw_input":        task,
            "schema":           {"data": "Extracted data from the input", "summary": "Brief summary"},
            "extraction_mode":  "auto",
            "extracted_json":   {},
            "validation_passed": False,
            "issues":           [],
        })
        return {"result": str(r.get("extracted_json", "")), "error": r.get("error")}

    elif role == "quality_validation":
        r = quality_validation_node({
            **base, "agent": role,
            "artifact": task, "criteria": "",
            "validation_report": "", "score": 0, "passed": False,
        })
        return {"result": r.get("quality_feedback", ""), "error": r.get("error")}

    elif role == "code_reviewer":
        tokens     = task.split()
        file_paths = [t.strip(",;") for t in tokens if ("/" in t or "." in t) and len(t) > 3]
        r = code_reviewer_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "file_paths": file_paths, "focus": "general", "code_review": "",
        })
        return {"result": r.get("code_review", ""), "error": r.get("error")}

    elif role == "dependency_audit":
        r = dependency_audit_node({
            **base, "agent": role,
            "repo_owner": repo_owner, "repo_name": repo_name,
            "focus": "general", "dependency_report": "",
        })
        return {"result": r.get("dependency_report", ""), "error": r.get("error")}

    elif role == "fullstack_architect":
        r = fullstack_architect_node({
            **base, "agent": role,
            "task": task, "stack_context": task, "framework": "nextjs",
            "output_type": "architecture_doc", "architecture_doc": "", "stack_decision": "",
        })
        return {"result": r.get("architecture_doc", ""), "error": r.get("error")}

    elif role == "database_architect":
        r = database_architect_node({
            **base, "agent": role,
            "task": task, "db_context": task, "db_engine": "postgresql",
            "output_type": "schema_design", "db_design": "", "schema_summary": "",
        })
        return {"result": r.get("db_design", ""), "error": r.get("error")}

    elif role == "supabase_specialist":
        r = supabase_specialist_node({
            **base, "agent": role,
            "task": task, "project_context": task, "area": "general",
            "output_type": "migration", "supabase_spec": "", "sql_output": "",
        })
        return {"result": r.get("supabase_spec", ""), "error": r.get("error")}

    elif role == "devops_engineer":
        r = devops_engineer_node({
            **base, "agent": role,
            "task": task, "infra_context": task, "platform": "hostinger",
            "output_type": "general", "devops_plan": "", "config_output": "",
        })
        return {"result": r.get("devops_plan", ""), "error": r.get("error")}

    elif role == "deployment_specialist":
        r = deployment_specialist_node({
            **base, "agent": role,
            "task": task, "deploy_context": task, "target": "hostinger_vps",
            "output_type": "deployment_runbook", "deployment_plan": "", "deploy_commands": "",
        })
        return {"result": r.get("deployment_plan", ""), "error": r.get("error")}

    elif role == "performance_auditor":
        r = performance_auditor_node({
            **base, "agent": role,
            "task": task, "perf_context": task, "target_platform": "web",
            "output_type": "general", "perf_report": "", "score_summary": "",
        })
        return {"result": r.get("perf_report", ""), "error": r.get("error")}

    elif role == "mcp_builder":
        r = mcp_builder_node({
            **base, "agent": role,
            "task": task, "mcp_context": task, "transport": "stdio",
            "output_type": "server_spec", "mcp_spec": "", "server_code": "",
        })
        return {"result": r.get("mcp_spec", ""), "error": r.get("error")}

    elif role == "gcp_ai_specialist":
        r = gcp_ai_specialist_node({
            **base, "agent": role,
            "task": task, "gcp_context": task, "gcp_service": "vertex_ai",
            "output_type": "agent_architecture", "gcp_spec": "", "terraform_output": "",
        })
        return {"result": r.get("gcp_spec", ""), "error": r.get("error")}

    elif role == "data_parser":
        r = data_parser_node({
            **base, "agent": role,
            "raw_input": task, "target_format": "json",
            "parsed_output": "", "parse_confidence": 0,
        })
        return {"result": r.get("parsed_output", ""), "error": r.get("error")}

    elif role == "agent_builder":
        r = agent_builder_node({
            **base, "agent": role,
            "agent_brief": task, "role_name": "",
            "skill_file": "", "node_file": "",
        })
        result = r.get("skill_file") or r.get("node_file") or ""
        return {"result": result, "error": r.get("error")}

    elif role == "document_qa":
        r = document_qa_node({
            **base, "agent": role,
            "question": task, "documents": state.get("documents", task),
            "chunk_size": 1000, "top_k": 5,
            "answer": "", "sources": "", "confidence": 0,
        })
        return {"result": r.get("answer", ""), "error": r.get("error")}

    elif role == "vision_analyst":
        r = vision_analyst_node({
            **base, "agent": role,
            "task": task, "image_url": state.get("image_url", ""),
            "analysis_type": state.get("analysis_type", "describe"),
            "analysis": "", "findings": "", "confidence": 0,
        })
        return {"result": r.get("analysis", ""), "error": r.get("error")}

    elif role == "pipeline_monitor":
        r = pipeline_monitor_node({
            **base, "agent": role,
            "log_data": task, "pipeline_name": "general",
            "pipeline_type": "general", "expected_behaviour": "",
            "output_type": "diagnosis", "thread_id": "",
            "signal_summary": "", "diagnosis": "", "alert_level": 0,
            "action_items": [], "silent_failure_detected": False, "checklist": [],
        })
        return {"result": r.get("signal_summary", ""), "error": r.get("error")}

    elif role == "process_auditor":
        r = process_auditor_node({
            **base, "agent": role,
            "process_description": task, "process_type": "general",
            "output_type": "friction_report",
            "audit_report": "", "friction_count": 0, "bottleneck_score": 0,
        })
        return {"result": r.get("audit_report", ""), "error": r.get("error")}

    elif role == "truth_verifier":
        r = truth_verifier_node({
            **base, "agent": role,
            "artifact": task, "artifact_type": "general", "check_level": "standard_audit",
            "verification_report": "", "gates_passed": 0, "gates_failed": 0,
        })
        return {"result": r.get("verification_report", ""), "error": r.get("error")}

    # ── Content & Creative agents ─────────────────────────────────────────────

    elif role == "social_post_generator":
        r = social_post_generator_node({
            **base, "agent": role,
            "brief": task, "platform": "facebook", "tone": "professional",
            "hashtags": "#JaiOS6 #JonnyAI", "publish": False,
            "image_url": None, "post_copy": {}, "published": False, "post_ids": {},
        })
        copy = r.get("post_copy", {})
        return {"result": copy.get("facebook") or copy.get("instagram") or "",
                "error": r.get("error")}

    elif role == "content_scaler":
        r = content_scaler_node({
            **base, "agent": role,
            "topic": task,
            "brand_voice": "professional, clear, results-first",
            "platform": "linkedin",
            "variant_count": 3,
            "cta": "",
            "variants": [],
        })
        variants = r.get("variants", [])
        return {"result": "\n\n---\n\n".join(variants), "error": r.get("error")}

    elif role == "ad_copy_writer":
        r = ad_copy_writer_node({
            **base, "agent": role,
            "product": task, "audience": "Our target customer",
            "platform": "facebook", "objective": "conversion",
            "tone": "direct", "usp": "", "cta": "Learn More",
            "ad_copy": {},
        })
        copy = r.get("ad_copy", {})
        result = copy.get("headline", "") + "\n\n" + copy.get("body", "") if copy else ""
        return {"result": result, "error": r.get("error")}

    elif role == "copywriter":
        r = copywriter_node({
            **base, "agent": role,
            "task":         task,
            "brand_context": "",
            "output_type":  "headline_variants",
            "copy_format":  "direct_response",
            "copy_output":  "",
            "headline":     "",
        })
        return {"result": r.get("copy_output", ""), "error": r.get("error")}

    elif role == "brand_voice_guide":
        r = brand_voice_guide_node({
            **base, "agent": role,
            "brand_name": repo_owner or "Brand",
            "brand_context": task,
            "target_audience": "target customers",
            "tone_keywords":   "professional, clear, engaging",
            "examples":        "",
            "brand_voice_guide": "",
        })
        return {"result": r.get("brand_voice_guide", ""), "error": r.get("error")}

    elif role == "creative_director":
        r = creative_director_node({
            **base, "agent": role,
            "task": task, "brand_context": task, "medium": "general",
            "output_type": "campaign_concept",
            "creative_brief": "", "direction_notes": "",
        })
        return {"result": r.get("creative_brief", ""), "error": r.get("error")}

    elif role == "video_brief_writer":
        r = video_brief_writer_node({
            **base, "agent": role,
            "topic": task, "platform": "general", "duration_seconds": 60,
            "hook_style": "direct", "cta": "Follow for more",
            "brand_context": "", "video_brief": "",
        })
        return {"result": r.get("video_brief", ""), "error": r.get("error")}

    elif role == "voice_synthesiser":
        r = voice_synthesiser_node({
            **base, "agent": role,
            "script_brief": task, "voice_use": "narration",
            "tone_style": "professional", "duration_target_seconds": 60,
            "production_script": "", "voice_direction": "",
        })
        return {"result": r.get("production_script", ""), "error": r.get("error")}

    elif role == "ui_designer":
        r = ui_designer_node({
            **base, "agent": role,
            "task": task, "design_context": task, "component_type": "general",
            "output_type": "component_spec",
            "design_spec": "", "component_code": "",
        })
        return {"result": r.get("design_spec", ""), "error": r.get("error")}

    elif role == "content_auditor":
        r = content_auditor_node({
            **base, "agent": role,
            "content": task, "content_type": "general", "audit_focus": "depth",
            "audit_report": "", "depth_score": 0, "fluff_count": 0,
        })
        return {"result": r.get("audit_report", ""), "error": r.get("error")}

    elif role == "pr_writer":
        r = pr_writer_node({
            **base, "agent": role,
            "announcement": task, "company": repo_owner,
            "contact_name": "", "contact_email": "",
            "press_release": "",
        })
        return {"result": r.get("pr_content", ""), "error": r.get("error")}

    # ── Business & Strategy agents ────────────────────────────────────────────

    elif role == "brief_writer":
        r = brief_writer_node({
            **base, "agent": role,
            "client_name": repo_owner, "brief_type": "proposal",
            "context": task,
            "goal": "Create a professional brief based on the provided context.",
            "budget_hint": "", "timeline_hint": "", "brief": "",
        })
        return {"result": r.get("brief", ""), "error": r.get("error")}

    elif role == "proposal_writer":
        r = proposal_writer_node({
            **base, "agent": role,
            "client_name": repo_owner, "service_context": task,
            "budget_hint": "", "timeline_hint": "",
            "proposal": "",
        })
        return {"result": r.get("proposal", ""), "error": r.get("error")}

    elif role == "monetisation_strategist":
        r = monetisation_strategist_node({
            **base, "agent": role,
            "client_name": repo_owner,
            "business_context": task,
            "current_revenue": "",
            "goals": "Maximise revenue and build sustainable growth.",
            "constraints": "",
            "strategy": "",
        })
        return {"result": r.get("strategy", ""), "error": r.get("error")}

    elif role == "pricing_strategist":
        r = pricing_strategist_node({
            **base, "agent": role,
            "product": task, "business_context": "",
            "competitor_context": "", "audience": "",
            "pricing_strategy": "",
        })
        return {"result": r.get("pricing_strategy", ""), "error": r.get("error")}

    elif role == "product_strategist":
        import re as _re
        _pname = task.split(" for ")[0] if " for " in task else task[:50]
        _pname = _re.sub(r"^(build|create|write|design|make|develop)\s+(a\s+)?", "", _pname, flags=_re.IGNORECASE).strip()
        r = product_strategist_node({
            **base, "agent": role,
            "task": task, "product_name": _pname or task[:50],
            "stage": "mvp", "goal": task,
            "user_pain": "Identified from task context — see full task for detail.",
            "strategy_output": "", "framework_used": "",
        })
        return {"result": r.get("strategy_output", ""), "error": r.get("error")}

    elif role == "sales_conversion":
        r = sales_conversion_node({
            **base, "agent": role,
            "prospect_name": repo_owner,
            "company": repo_name,
            "deal_stage": "engaged",
            "context": task,
            "objections": "",
            "close_strategy": "",
        })
        return {"result": r.get("close_strategy", ""), "error": r.get("error")}

    elif role == "funnel_architect":
        r = funnel_architect_node({
            **base, "agent": role,
            "product": task, "audience": "Our target customer",
            "funnel_stage": "consideration", "traffic_source": "organic_search",
            "avg_order_value": "unknown", "current_conversion": "unknown",
            "funnel_spec": "",
        })
        return {"result": r.get("funnel_spec", ""), "error": r.get("error")}

    elif role == "ecommerce_strategist":
        r = ecommerce_strategist_node({
            **base, "agent": role,
            "product_name":     task,
            "brief":            task,
            "platform":         "shopify",
            "business_context": "",
            "ecommerce_strategy": "", "strategy_output": "",
        })
        return {"result": r.get("strategy_output", ""), "error": r.get("error")}

    elif role == "launch_orchestrator":
        # Ralph Loop 4: Intent extraction — NL to structured fields
        fields = extract_intent(task, state.get("brief", ""), role)
        log.info("intent_extractor.launch", extracted=fields)
        r = launch_orchestrator_node({
            **base, "agent": role,
            "product_name":  fields.get("product_name", task[:80]),
            "launch_type":   fields.get("launch_type", "saas_product"),
            "channels":      fields.get("channels", "all"),
            "launch_date":   fields.get("launch_date", ""),
            "audience":      fields.get("audience", ""),
            "launch_plan":   "",
            "thread_id":     workflow_id,
        })
        return {"result": r.get("launch_plan", ""), "error": r.get("error")}

    elif role == "venture_ideator":
        r = venture_ideator_node({
            **base, "agent": role,
            "idea_context":    task,
            "idea_type":       "general",
            "market_size":     "niche",
            "budget_hint":     "lean",
            "venture_blueprint": "",
            "viability_score": 0,
        })
        return {"result": r.get("venture_blueprint", ""), "error": r.get("error")}

    elif role == "investor_pitch_writer":
        r = investor_pitch_writer_node({
            **base, "agent": role,
            "company": repo_owner, "brief": task,
            "stage": "seed", "ask": "",
            "pitch": "",
        })
        return {"result": r.get("pitch_content", ""), "error": r.get("error")}

    # ── Intelligence & Research agents ────────────────────────────────────────

    elif role == "business_intelligence":
        r = business_intelligence_node({
            **base, "agent": role,
            "client_name": repo_owner,
            "kpi_data": task,
            "period": "Current period",
            "goals": "",
            "context": "",
            "bi_report": "",
        })
        return {"result": r.get("bi_report", ""), "error": r.get("error")}

    elif role == "analytics_reporter":
        r = analytics_reporter_node({
            **base, "agent": role,
            "raw_data": task, "focus": "general",
            "period": "30d", "goal": "",
            "trends": {}, "data_ready": False,
            "analytics_report": "", "key_metrics": {},
        })
        return {"result": r.get("analytics_report", ""), "error": r.get("error")}

    elif role == "research_analyst":
        r = research_analyst_node({
            **base, "agent": role,
            "question": task, "research_type": "general",
            "depth": "standard_report", "sources": "",
            "domain": "", "thread_id": workflow_id,
            "framework": "", "depth_spec": {},
            "research_report": "", "confidence_score": 0,
        })
        return {"result": r.get("research_report", ""), "error": r.get("error")}

    elif role == "competitor_monitor":
        r = competitor_monitor_node({
            **base, "agent": role,
            "competitor_url": task.split()[0] if task.startswith("http") else "https://example.com",
            "our_context": task, "focus": "general", "intel_report": "",
        })
        return {"result": r.get("intel_report", ""), "error": r.get("error")}

    elif role == "seo_specialist":
        r = seo_specialist_node({
            **base, "agent": role,
            "url": task, "page_content": task,
            "target_keywords": "", "business_context": "", "focus": "general",
            "seo_report": "",
        })
        return {"result": r.get("seo_report", ""), "error": r.get("error")}

    elif role == "supabase_intelligence":
        r = supabase_intelligence_node({
            **base, "agent": role,
            "query": task, "focus": "general", "intelligence": "",
        })
        return {"result": r.get("intelligence", ""), "error": r.get("error")}

    # ── Operations & Delivery agents ──────────────────────────────────────────

    elif role == "automation_architect":
        r = automation_architect_node({
            **base, "agent": role,
            "workflow_description": task,
            "tools_available": "n8n, Resend, Supabase, OpenAI",
            "trigger_type": "webhook",
            "complexity": "medium",
            "automation_spec": "",
        })
        return {"result": r.get("automation_spec", ""), "error": r.get("error")}

    elif role == "email_architect":
        r = email_architect_node({
            **base, "agent": role,
            "sequence_goal": "nurture", "audience": task,
            "product": "Our product/service", "num_emails": 3,
            "tone": "professional", "from_name": "The Team",
            "email_sequence": "", "email_count": 0,
        })
        return {"result": r.get("email_sequence", ""), "error": r.get("error")}

    elif role == "project_manager":
        r = project_manager_node({
            **base, "agent": role,
            "task": task, "team_context": "",
            "timeline_hint": "", "project_plan": "",
        })
        return {"result": r.get("pm_output", ""), "error": r.get("error")}

    elif role == "customer_success":
        r = customer_success_node({
            **base, "agent": role,
            "customer_name": repo_owner or "Customer",
            "input_text":    task,
            "context":       task,
            "health_score":  0,
            "cs_report":     "", "cs_output": "",
        })
        return {"result": r.get("cs_output", ""), "error": r.get("error")}

    elif role == "knowledge_base_writer":
        r = knowledge_base_writer_node({
            **base, "agent": role,
            "topic": task, "audience": "end users",
            "format": "help_article", "kb_article": "",
        })
        return {"result": r.get("document", ""), "error": r.get("error")}

    elif role == "case_study_writer":
        r = case_study_writer_node({
            **base, "agent": role,
            "client_name":   repo_owner or "Client",
            "problem":       task,
            "solution":      task,
            "results":       "",
            "case_study":    "",
        })
        return {"result": r.get("case_study", ""), "error": r.get("error")}

    elif role == "course_designer":
        r = course_designer_node({
            **base, "agent": role,
            "course_title":   task,
            "topic":          task,
            "transformation": f"Master the core skills needed for {task}",
            "target_student": "motivated learners with basic background knowledge",
            "audience":       "general",
            "depth":          "intermediate",
            "format":         "online_course",
            "course_outline": "", "curriculum": "",
        })
        return {"result": r.get("curriculum", ""), "error": r.get("error")}

    elif role == "chatbot_designer":
        r = chatbot_designer_node({
            **base, "agent": role,
            "bot_name":    "Assistant",
            "bot_purpose": task,
            "audience":    "customers",
            "platform":    "website",
            "tone":        "friendly",
            "chatbot_flow": "",
            "chatbot_design": "",
        })
        return {"result": r.get("chatbot_design", ""), "error": r.get("error")}

    elif role == "persona_builder":
        r = persona_builder_node({
            **base, "agent": role,
            "product_name":        task,
            "product_description": task,
            "brief":               task,
            "business_context":    "",
            "num_personas":        2,
            "personas":            "",
        })
        return {"result": r.get("personas", ""), "error": r.get("error")}

    elif role == "financial_analyst":
        r = financial_analyst_node({
            **base, "agent": role,
            "task": task, "focus": "general",
            "period": "current", "financial_report": "",
        })
        return {"result": r.get("financial_report", ""), "error": r.get("error")}

    elif role == "legal_advisor":
        r = legal_advisor_node({
            **base, "agent": role,
            "task": task, "jurisdiction": "UK",
            "context": "", "legal_report": "",
        })
        return {"result": r.get("legal_advice", ""), "error": r.get("error")}

    elif role == "fact_checker":
        r = fact_checker_node({
            **base, "agent": role,
            "claim": task, "context": "",
            "fact_check_report": "", "verdict": "",
        })
        return {"result": r.get("fact_check_report", ""), "error": r.get("error")}

    elif role == "ab_test_designer":
        r = ab_test_designer_node({
            **base, "agent": role,
            "page_or_element":  task,
            "hypothesis":       f"We believe changing {task} will improve conversion",
            "baseline_cvr":     0.03,
            "mde":              0.20,
            "daily_visitors":   1000,
            "test_type":        "ab",
            "output_type":      "full_design",
            "test_design":      "", "sample_size": 0,
            "runtime_days":     0, "target_cvr":   0.0,
            "test_guidance":    "",
        })
        return {"result": r.get("test_design", ""), "error": r.get("error")}

    elif role == "pr_writer":
        r = pr_writer_node({
            **base, "agent": role,
            "announcement": task, "company": repo_owner,
            "contact_name": "", "contact_email": "",
            "press_release": "",
        })
        return {"result": r.get("pr_content", ""), "error": r.get("error")}


    # ── Batch 6: awesome-llm-apps inspired agents ─────────────────────────

    elif role == "ux_researcher":
        r = ux_researcher_node({
            **base, "agent": role,
            "task": task, "research_type": "general",
            "ux_report": "",
        })
        return {"result": r.get("ux_report", ""), "error": r.get("error")}

    elif role == "senior_developer":
        r = senior_developer_node({
            **base, "agent": role,
            "task": task, "language": "python",
            "code_output": "",
        })
        return {"result": r.get("code_output", ""), "error": r.get("error")}

    elif role == "system_architect":
        r = system_architect_node({
            **base, "agent": role,
            "task": task, "scope": "general",
            "architecture_report": "",
        })
        return {"result": r.get("architecture_report", ""), "error": r.get("error")}

    elif role == "investment_analyst":
        r = investment_analyst_node({
            **base, "agent": role,
            "task": task, "analysis_type": "general",
            "investment_report": "",
        })
        return {"result": r.get("investment_report", ""), "error": r.get("error")}

    elif role == "recruitment_specialist":
        r = recruitment_specialist_node({
            **base, "agent": role,
            "task": task, "recruitment_type": "general",
            "recruitment_report": "",
        })
        return {"result": r.get("recruitment_report", ""), "error": r.get("error")}

    elif role == "sales_intelligence":
        r = sales_intelligence_node({
            **base, "agent": role,
            "task": task, "research_type": "general",
            "sales_report": "",
        })
        return {"result": r.get("sales_report", ""), "error": r.get("error")}

    elif role == "legal_analyst":
        r = legal_analyst_node({
            **base, "agent": role,
            "task": task, "analysis_type": "general",
            "legal_report": "",
        })
        return {"result": r.get("legal_report", ""), "error": r.get("error")}

    elif role == "due_diligence_analyst":
        r = due_diligence_analyst_node({
            **base, "agent": role,
            "task": task, "dd_type": "general",
            "dd_report": "",
        })
        return {"result": r.get("dd_report", ""), "error": r.get("error")}

    elif role == "deep_researcher":
        r = deep_researcher_node({
            **base, "agent": role,
            "task": task, "research_scope": "comprehensive",
            "research_report": "",
        })
        return {"result": r.get("research_report", ""), "error": r.get("error")}

    elif role == "product_launch_strategist":
        r = product_launch_strategist_node({
            **base, "agent": role,
            "task": task, "launch_phase": "planning",
            "launch_plan": "",
        })
        return {"result": r.get("launch_plan", ""), "error": r.get("error")}

    elif role == "financial_planner":
        r = financial_planner_node({
            **base, "agent": role,
            "task": task, "planning_type": "general",
            "financial_plan": "",
        })
        return {"result": r.get("financial_plan", ""), "error": r.get("error")}
    elif role == "api_integration_agent":
        r = api_integration_agent_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "betting_systems":
        r = betting_systems_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "code_executor":
        r = code_executor_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "cost_tracker":
        r = cost_tracker_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "darts_analyst":
        r = darts_analyst_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "error_recovery_agent":
        r = error_recovery_agent_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "eval_judge":
        r = eval_judge_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "feedback_collector":
        r = feedback_collector_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "football_tactical":
        r = football_tactical_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "formula1_analyst":
        r = formula1_analyst_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "horse_racing":
        r = horse_racing_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "human_gate":
        r = human_gate_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "image_prompt_engineer":
        r = image_prompt_engineer_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "motogp_analyst":
        r = motogp_analyst_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "onboarding_agent":
        r = onboarding_agent_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "rag_retriever":
        r = rag_retriever_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "risk_analyst":
        r = risk_analyst_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "roulette_math":
        r = roulette_math_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "summariser":
        r = summariser_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "translator":
        r = translator_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    elif role == "workflow_planner":
        r = workflow_planner_node({
            **base, "agent": role, "task": task,
        })
        return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
    else:
        # Fallback to research_analyst for unknown roles
        try:
            r = research_analyst_node({**base, "agent": "research_analyst", "task": task, "research_scope": "comprehensive", "research_report": ""})
            return {"result": r.get("output", r.get("result", "")), "error": r.get("error")}
        except Exception as e:
            return {"result": f"Fallback failed: {e}", "error": str(e)}



def execute_pipeline(state: SupervisorState) -> dict:
    """Execute a pipeline of agents sequentially, passing accumulated results."""
    pipeline = state.get("pipeline")
    task = state.get("task", "")

    # Single-agent execution (backwards compatible)
    if not pipeline:
        return execute_single_agent(state)

    # Multi-agent pipeline execution
    accumulated_results = []
    final_error = None

    for i, role in enumerate(pipeline):
        step_num = i + 1
        total_steps = len(pipeline)
        log.info("pipeline.step", step=step_num, total=total_steps, role=role)

        if accumulated_results:
            context_parts = []
            for r in accumulated_results:
                context_parts.append(f"--- Output from {r['role']} (step {r['step']}/{total_steps}) ---\n{r['result']}")
            context_prefix = "\n\n".join(context_parts)
            enriched_task = (
                f"PIPELINE EXECUTION - Step {step_num}/{total_steps} - Role: {role}\n\n"
                f"ORIGINAL BRIEF:\n{task}\n\n"
                f"PRIOR AGENT OUTPUTS (use as context, build on them, do NOT repeat):\n{context_prefix}\n\n"
                f"YOUR TASK: As the {role}, produce your specific contribution. Focus on YOUR speciality."
            )
        else:
            enriched_task = (
                f"PIPELINE EXECUTION - Step {step_num}/{total_steps} - Role: {role}\n\n"
                f"BRIEF:\n{task}\n\n"
                f"YOUR TASK: As the {role}, produce your specific contribution. "
                f"You are the first agent in a {total_steps}-agent pipeline."
            )

        agent_state = {**state, "task": enriched_task, "selected_role": role, "pipeline": None}
        try:
            result = execute_single_agent(agent_state)
            agent_output = result.get("result", "")
            accumulated_results.append({
                "role": role, "step": step_num,
                "result": agent_output, "error": result.get("error"),
            })
            if result.get("error"):
                log.warning("pipeline.step_error", step=step_num, role=role, error=result["error"])
        except Exception as e:
            log.error("pipeline.step_exception", step=step_num, role=role, error=str(e))
            accumulated_results.append({
                "role": role, "step": step_num,
                "result": f"[Agent {role} failed: {str(e)}]", "error": str(e),
            })

    separator = "=" * 60
    combined_parts = []
    for r in accumulated_results:
        combined_parts.append(f"{separator}\n## {r['role'].upper()} (Step {r['step']}/{len(pipeline)})\n{separator}\n{r['result']}")
    combined = "\n\n".join(combined_parts)

    try:
        notifier = TelegramNotifier()
        roles_str = " -> ".join(pipeline)
        err_count = sum(1 for r in accumulated_results if r["error"])
        notifier.send(f"Pipeline complete: {roles_str}\nSteps: {len(pipeline)} | Errors: {err_count}")
    except Exception:
        pass

    return {"result": combined, "error": final_error}


def build_supervisor():
    """Build and compile the supervisor graph."""
    graph = StateGraph(SupervisorState)
    graph.add_node("route",   route_node)
    graph.add_node("execute", execute_pipeline, retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0))
    graph.add_edge(START,     "route")
    graph.add_edge("route",   "execute")
    graph.add_edge("execute",  END)
    # Try PostgresSaver (persistent), fallback to MemorySaver (RAM)
    db_url = os.environ.get("BRAIN_CONNECTION_STRING", "")
    if db_url:
        try:
            # Use session-mode pooler (port 5432) for prepared statements
            session_url = db_url.replace(":6543/", ":5432/")
            if "sslmode" not in session_url:
                session_url += "?sslmode=require"
            checkpointer = PostgresSaver.from_conn_string(session_url)
            checkpointer.setup()
            log.info("supervisor.checkpointer", type="PostgresSaver", db="supabase")
            return graph.compile(checkpointer=checkpointer)
        except Exception as e:
            log.warning("supervisor.postgres_fallback", error=str(e))
    log.info("supervisor.checkpointer", type="MemorySaver", reason="no_db_url_or_error")
    return graph.compile(checkpointer=MemorySaver())



# ── Public API ────────────────────────────────────────────────────────────────

_supervisor_graph = None

def run_supervisor(state: dict) -> dict:
    """
    Entry point for api/main.py.
    Lazily builds the supervisor graph and invokes it.
    state must include: workflow_id, task (or request)
    """
    global _supervisor_graph
    if _supervisor_graph is None:
        _supervisor_graph = build_supervisor()

    # Normalise 'request' or 'brief' → 'task'
    task = state.get("task") or state.get("request") or state.get("brief") or ""
    wf_id = state.get("workflow_id", str(uuid.uuid4()))
    input_state = {
        "workflow_id": wf_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "agent":       "orchestrator",
        "task":        task,
        "error":       None,
        "selected_role": "",
        "result":      "",
        "client_id":   state.get("client_id", ""),
        "project_id":  state.get("project_id", ""),
    }

    cfg = {"configurable": {"thread_id": wf_id}}
    return _supervisor_graph.invoke(input_state, config=cfg)



def run_pipeline_supervisor(state: dict) -> dict:
    """
    Entry point for pipeline execution from api/main.py.
    state must include: pipeline (str), task (str)
    Optional: custom_steps (list[str]), eval_output (bool), client_id, project_id
    """
    from graphs.pipeline_engine import run_pipeline
    return run_pipeline(
        pipeline_name=state.get("pipeline", ""),
        task=state.get("task", state.get("brief", "")),
        client_id=state.get("client_id", ""),
        project_id=state.get("project_id", ""),
        eval_output=state.get("eval_output", True),
        custom_steps=state.get("custom_steps"),
    )

if __name__ == "__main__":
    import sys

    task  = sys.argv[1] if len(sys.argv) > 1 else "Review the architecture of JaiO.S-6.0-Experimental"
    owner = sys.argv[2] if len(sys.argv) > 2 else "jonnyallum"
    repo  = sys.argv[3] if len(sys.argv) > 3 else "JaiO.S-6.0-Experimental"

    app    = build_supervisor()
    wf_id  = str(uuid.uuid4())
    result = app.invoke(
        {
            "workflow_id": wf_id,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "agent":       "orchestrator",
            "error":       None,
            "task":        task,
            "repo_owner":  owner,
            "repo_name":   repo,
            "selected_role": "",
            "result":      "",
        },
        config={"configurable": {"thread_id": wf_id}},
    )

    print(f"\n=== Supervisor Result ===")
    print(f"Role selected : {result['selected_role']}")
    print(f"\n{result['result'][:1000]}")
