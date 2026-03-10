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
    "business_intelligence_node",   "BIReportState",
]
