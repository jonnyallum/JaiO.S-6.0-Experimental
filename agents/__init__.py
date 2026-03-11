"""
Agentic skill modules — role-based, persona-independent.
Persona injection (name, nickname, personality) is handled by personas/config.py.
"""
# ── Core technical agents ─────────────────────────────────────────────────────────
from agents.github_intelligence import github_intelligence_node, GitHubIntelState
from agents.security_audit import security_audit_node, SecurityAuditState
from agents.architecture_review import architecture_review_node, ArchitectureReviewState
from agents.data_extraction import data_extraction_node, DataExtractionState
from agents.quality_validation import quality_validation_node, QualityValidationState
from agents.brief_writer import brief_writer_node, BriefWriterState
from agents.code_reviewer import code_reviewer_node, CodeReviewState
from agents.dependency_audit import dependency_audit_node, DependencyAuditState
from agents.social_post_generator import social_post_generator_node, SocialPostState
from agents.supabase_intelligence import supabase_intelligence_node, BrainIntelState

# ── Business intelligence agents ──────────────────────────────────────────────────
from agents.monetisation_strategist import monetisation_strategist_node, MonetisationState
from agents.sales_conversion import sales_conversion_node, SalesConversionState
from agents.content_scaler import content_scaler_node, ContentScalerState
from agents.automation_architect import automation_architect_node, AutomationState
from agents.business_intelligence import business_intelligence_node, BIReportState

# ── Growth & intelligence agents ──────────────────────────────────────────────────
from agents.seo_specialist import seo_specialist_node, SEOState
from agents.competitor_monitor import competitor_monitor_node, CompetitorIntelState
from agents.email_architect import email_architect_node, EmailSequenceState
from agents.video_brief_writer import video_brief_writer_node, VideoBriefState
from agents.funnel_architect import funnel_architect_node, FunnelState


# Batch 2 — Analytics, Marketing & Builder agents
from agents.analytics_reporter import analytics_reporter_node, AnalyticsState
from agents.ad_copy_writer import ad_copy_writer_node, AdCopyState
from agents.launch_orchestrator import launch_orchestrator_node, LaunchState
from agents.case_study_writer import case_study_writer_node, CaseStudyState
from agents.knowledge_base_writer import knowledge_base_writer_node, KnowledgeBaseState
from agents.agent_builder import agent_builder_node, AgentBuilderState

__all__ = [
    # Core technical
    "github_intelligence_node", "GitHubIntelState",
    "security_audit_node",      "SecurityAuditState",
    "architecture_review_node", "ArchitectureReviewState",
    "data_extraction_node",     "DataExtractionState",
    "quality_validation_node",  "QualityValidationState",
    "brief_writer_node",        "BriefWriterState",
    "code_reviewer_node",       "CodeReviewState",
    "dependency_audit_node",    "DependencyAuditState",
    "social_post_generator_node", "SocialPostState",
    "supabase_intelligence_node", "BrainIntelState",
    # Business intelligence
    "monetisation_strategist_node", "MonetisationState",
    "sales_conversion_node",        "SalesConversionState",
    "content_scaler_node",          "ContentScalerState",
    "automation_architect_node",    "AutomationState",
    "business_intelligence_node",   "BIReportState",    # Growth & intelligence
    "seo_specialist_node",      "SEOState",
    "competitor_monitor_node",  "CompetitorIntelState",
    "email_architect_node",     "EmailSequenceState",
    "video_brief_writer_node",  "VideoBriefState",
    "funnel_architect_node",    "FunnelState",
    "analytics_reporter_node", "AnalyticsState",
    "ad_copy_writer_node", "AdCopyState",
    "launch_orchestrator_node", "LaunchState",
    "case_study_writer_node", "CaseStudyState",
    "knowledge_base_writer_node", "KnowledgeBaseState",
    "agent_builder_node", "AgentBuilderState",

    "proposal_writer_node", "ProposalState",
    "product_strategist_node", "ProductStrategyState",
    "pricing_strategist_node", "PricingState",
    "course_designer_node", "CourseState",
    "chatbot_designer_node", "ChatbotState",

    "persona_builder_node", "PersonaState",
    "pr_writer_node", "PRState",
    "ab_test_designer_node", "ABTestState",
    "investor_pitch_writer_node", "InvestorPitchState",
    "brand_voice_guide_node", "BrandVoiceState",

]

# Batch 3 — Strategy, Design & Education agents
from agents.proposal_writer import proposal_writer_node, ProposalState
from agents.product_strategist import product_strategist_node, ProductStrategyState
from agents.pricing_strategist import pricing_strategist_node, PricingState
from agents.course_designer import course_designer_node, CourseState
from agents.chatbot_designer import chatbot_designer_node, ChatbotState

# Batch 4 — Research, PR, CRO, Investment & Brand agents
from agents.persona_builder import persona_builder_node, PersonaState
from agents.pr_writer import pr_writer_node, PRState
from agents.ab_test_designer import ab_test_designer_node, ABTestState
from agents.investor_pitch_writer import investor_pitch_writer_node, InvestorPitchState
from agents.brand_voice_guide import brand_voice_guide_node, BrandVoiceState

# Batch 5 — E-commerce, Data Parsing, Research, Pipeline & Customer Success agents
from agents.ecommerce_strategist import ecommerce_strategist_node, EcommerceStrategistState
from agents.data_parser import data_parser_node, DataParserState
from agents.research_analyst import research_analyst_node, ResearchAnalystState
from agents.pipeline_monitor import pipeline_monitor_node, PipelineMonitorState
from agents.customer_success import customer_success_node, CustomerSuccessState

# Batch 6 — Quality, Venture & Voice agents
from agents.truth_verifier import truth_verifier_node, TruthVerifierState
from agents.content_auditor import content_auditor_node, ContentAuditorState
from agents.process_auditor import process_auditor_node, ProcessAuditorState
from agents.venture_ideator import venture_ideator_node, VentureIdeatorState
from agents.voice_synthesiser import voice_synthesiser_node, VoiceSynthesiserState

# Batch 7 — Dev/Tech: Fullstack, DB, Supabase, DevOps, Deployment
from agents.fullstack_architect import fullstack_architect_node, FullstackArchitectState
from agents.database_architect import database_architect_node, DatabaseArchitectState
from agents.supabase_specialist import supabase_specialist_node, SupabaseSpecialistState
from agents.devops_engineer import devops_engineer_node, DevOpsEngineerState
from agents.deployment_specialist import deployment_specialist_node, DeploymentSpecialistState

# Batch 8 — Performance, MCP, GCP, UI Design, Creative Direction
from agents.performance_auditor import performance_auditor_node, PerformanceAuditorState
from agents.mcp_builder import mcp_builder_node, McpBuilderState
from agents.gcp_ai_specialist import gcp_ai_specialist_node, GcpAiSpecialistState
from agents.ui_designer import ui_designer_node, UiDesignerState
from agents.creative_director import creative_director_node, CreativeDirectorState

# Batch 9 — Final close-out: Copy, PM, Finance, Legal, Fact-Check
from agents.copywriter import copywriter_node, CopywriterState
from agents.project_manager import project_manager_node, ProjectManagerState
from agents.financial_analyst import financial_analyst_node, FinancialAnalystState
from agents.legal_advisor import legal_advisor_node, LegalAdvisorState
from agents.fact_checker import fact_checker_node, FactCheckerState
