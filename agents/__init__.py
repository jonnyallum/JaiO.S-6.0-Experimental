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

]

# Batch 3 — Strategy, Design & Education agents
from agents.proposal_writer import proposal_writer_node, ProposalState
from agents.product_strategist import product_strategist_node, ProductStrategyState
from agents.pricing_strategist import pricing_strategist_node, PricingState
from agents.course_designer import course_designer_node, CourseState
from agents.chatbot_designer import chatbot_designer_node, ChatbotState
