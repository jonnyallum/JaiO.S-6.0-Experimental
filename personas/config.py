"""
Persona Config — injectable agent identity layer.

Skills live in agents/. Names, nicknames, and personalities live here.
To rebrand for any client: update .env or Supabase personas table.
Zero code changes required.

Usage:
    from personas.config import get_persona
    persona = get_persona("github_intelligence")
    # persona = {"name": "Hugo Reeves", "nickname": "The Crawler", "handle": "@hugo", "personality": "..."}
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersonaSettings(BaseSettings):
    """
    Optional env vars for persona names/nicknames.
    If not set, agents use their role slug as identity.
    Fully overridable per-deployment.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub Intelligence
    persona_github_intelligence_name: str = Field("")
    persona_github_intelligence_nickname: str = Field("")
    persona_github_intelligence_handle: str = Field("")
    persona_github_intelligence_personality: str = Field("")

    # Security Audit
    persona_security_audit_name: str = Field("")
    persona_security_audit_nickname: str = Field("")
    persona_security_audit_handle: str = Field("")
    persona_security_audit_personality: str = Field("")

    # Architecture Review
    persona_architecture_review_name: str = Field("")
    persona_architecture_review_nickname: str = Field("")
    persona_architecture_review_handle: str = Field("")
    persona_architecture_review_personality: str = Field("")

    # Data Extraction
    persona_data_extraction_name: str = Field("")
    persona_data_extraction_nickname: str = Field("")
    persona_data_extraction_handle: str = Field("")
    persona_data_extraction_personality: str = Field("")

    # Quality Validation
    persona_quality_validation_name: str = Field("")
    persona_quality_validation_nickname: str = Field("")
    persona_quality_validation_handle: str = Field("")
    persona_quality_validation_personality: str = Field("")

    # Brief Writer
    persona_brief_writer_name: str = Field("")
    persona_brief_writer_nickname: str = Field("")
    persona_brief_writer_handle: str = Field("")
    persona_brief_writer_personality: str = Field("")

    # Code Reviewer
    persona_code_reviewer_name: str = Field("")
    persona_code_reviewer_nickname: str = Field("")
    persona_code_reviewer_handle: str = Field("")
    persona_code_reviewer_personality: str = Field("")

    # Dependency Audit
    persona_dependency_audit_name: str = Field("")
    persona_dependency_audit_nickname: str = Field("")
    persona_dependency_audit_handle: str = Field("")
    persona_dependency_audit_personality: str = Field("")

    # Social Post Generator
    persona_social_post_generator_name: str = Field("")
    persona_social_post_generator_nickname: str = Field("")
    persona_social_post_generator_handle: str = Field("")
    persona_social_post_generator_personality: str = Field("")

    # Supabase Intelligence
    persona_supabase_intelligence_name: str = Field("")
    persona_supabase_intelligence_nickname: str = Field("")
    persona_supabase_intelligence_handle: str = Field("")
    persona_supabase_intelligence_personality: str = Field("")

    # Monetisation Strategist
    persona_monetisation_strategist_name: str = Field("")
    persona_monetisation_strategist_nickname: str = Field("")
    persona_monetisation_strategist_handle: str = Field("")
    persona_monetisation_strategist_personality: str = Field("")

    # Sales Conversion
    persona_sales_conversion_name: str = Field("")
    persona_sales_conversion_nickname: str = Field("")
    persona_sales_conversion_handle: str = Field("")
    persona_sales_conversion_personality: str = Field("")

    # Content Scaler
    persona_content_scaler_name: str = Field("")
    persona_content_scaler_nickname: str = Field("")
    persona_content_scaler_handle: str = Field("")
    persona_content_scaler_personality: str = Field("")

    # Automation Architect
    persona_automation_architect_name: str = Field("")
    persona_automation_architect_nickname: str = Field("")
    persona_automation_architect_handle: str = Field("")
    persona_automation_architect_personality: str = Field("")

    # Business Intelligence
    persona_business_intelligence_name: str = Field("")
    persona_business_intelligence_nickname: str = Field("")
    persona_business_intelligence_handle: str = Field("")
    persona_business_intelligence_personality: str = Field("")


    # SEO Specialist
    persona_seo_specialist_name: str = Field("")
    persona_seo_specialist_nickname: str = Field("")
    persona_seo_specialist_handle: str = Field("")
    persona_seo_specialist_personality: str = Field("")

    # Competitor Monitor
    persona_competitor_monitor_name: str = Field("")
    persona_competitor_monitor_nickname: str = Field("")
    persona_competitor_monitor_handle: str = Field("")
    persona_competitor_monitor_personality: str = Field("")

    # Email Architect
    persona_email_architect_name: str = Field("")
    persona_email_architect_nickname: str = Field("")
    persona_email_architect_handle: str = Field("")
    persona_email_architect_personality: str = Field("")

    # Video Brief Writer
    persona_video_brief_writer_name: str = Field("")
    persona_video_brief_writer_nickname: str = Field("")
    persona_video_brief_writer_handle: str = Field("")
    persona_video_brief_writer_personality: str = Field("")

    # Funnel Architect
    persona_funnel_architect_name: str = Field("")
    persona_funnel_architect_nickname: str = Field("")
    persona_funnel_architect_handle: str = Field("")
    persona_funnel_architect_personality: str = Field("")
    # Orchestrator
    persona_orchestrator_name: str = Field("")
    persona_orchestrator_nickname: str = Field("")
    persona_orchestrator_handle: str = Field("")
    persona_orchestrator_personality: str = Field("")


_persona_settings = PersonaSettings()

# Role -> env field prefix mapping
_ROLE_MAP: dict[str, str] = {
    # Core technical
    "github_intelligence":    "persona_github_intelligence",
    "security_audit":         "persona_security_audit",
    "architecture_review":    "persona_architecture_review",
    "data_extraction":        "persona_data_extraction",
    "quality_validation":     "persona_quality_validation",
    "brief_writer":           "persona_brief_writer",
    "code_reviewer":          "persona_code_reviewer",
    "dependency_audit":       "persona_dependency_audit",
    "social_post_generator":  "persona_social_post_generator",
    "supabase_intelligence":  "persona_supabase_intelligence",
    # Business intelligence
    "monetisation_strategist": "persona_monetisation_strategist",
    "sales_conversion":        "persona_sales_conversion",
    "content_scaler":          "persona_content_scaler",
    "automation_architect":    "persona_automation_architect",
    "business_intelligence":   "persona_business_intelligence",
    # Growth & intelligence
    "seo_specialist":       "persona_seo_specialist",
    "competitor_monitor":   "persona_competitor_monitor",
    "email_architect":      "persona_email_architect",
    "video_brief_writer":   "persona_video_brief_writer",
    "funnel_architect":     "persona_funnel_architect",
    # Orchestration
    "orchestrator":            "persona_orchestrator",
    "analytics_reporter":   ("analytics_reporter_name", "analytics_reporter_nickname", "analytics_reporter_handle", "analytics_reporter_personality"),
    "ad_copy_writer":       ("ad_copy_writer_name", "ad_copy_writer_nickname", "ad_copy_writer_handle", "ad_copy_writer_personality"),
    "launch_orchestrator":  ("launch_orchestrator_name", "launch_orchestrator_nickname", "launch_orchestrator_handle", "launch_orchestrator_personality"),
    "case_study_writer":    ("case_study_writer_name", "case_study_writer_nickname", "case_study_writer_handle", "case_study_writer_personality"),
    "knowledge_base_writer":("knowledge_base_writer_name", "knowledge_base_writer_nickname", "knowledge_base_writer_handle", "knowledge_base_writer_personality"),
    "agent_builder":        ("agent_builder_name", "agent_builder_nickname", "agent_builder_handle", "agent_builder_personality"),
}


def get_persona(role: str) -> dict:
    """
    Resolve the persona for a given role slug.
    Falls back to role-based defaults if env vars are not configured.

    Returns:
        {
          "role":        str  # stable identifier, never changes
          "name":        str  # display name (e.g. "Hugo Reeves")
          "nickname":    str  # short label (e.g. "The Crawler")
          "handle":      str  # @ handle (e.g. "@hugo")
          "personality": str  # personality description for prompts
        }
    """
    prefix = _ROLE_MAP.get(role)
    if prefix:
        name        = getattr(_persona_settings, f"{prefix}_name", "")
        nickname    = getattr(_persona_settings, f"{prefix}_nickname", "")
        handle      = getattr(_persona_settings, f"{prefix}_handle", "")
        personality = getattr(_persona_settings, f"{prefix}_personality", "")
    else:
        name = nickname = handle = personality = ""

    role_display = role.replace("_", " ").title()
    return {
        "role":        role,
        "name":        name        or role_display,
        "nickname":    nickname    or "",
        "handle":      handle      or f"@{role.replace('_', '-')}",
        "personality": personality or f"You are the {role_display} specialist. Be precise, specific, and actionable.",
    }


def get_all_personas() -> dict[str, dict]:
    """Return all configured personas. Useful for dashboards and sync."""
    return {role: get_persona(role) for role in _ROLE_MAP    "proposal_writer":      ("proposal_writer_name", "proposal_writer_nickname", "proposal_writer_handle", "proposal_writer_personality"),
    "product_strategist":   ("product_strategist_name", "product_strategist_nickname", "product_strategist_handle", "product_strategist_personality"),
    "pricing_strategist":   ("pricing_strategist_name", "pricing_strategist_nickname", "pricing_strategist_handle", "pricing_strategist_personality"),
    "course_designer":      ("course_designer_name", "course_designer_nickname", "course_designer_handle", "course_designer_personality"),
    "chatbot_designer":     ("chatbot_designer_name", "chatbot_designer_nickname", "chatbot_designer_handle", "chatbot_designer_personality"),
}

    # proposal_writer
    proposal_writer_name: str = Field("")
    proposal_writer_nickname: str = Field("")
    proposal_writer_handle: str = Field("")
    proposal_writer_personality: str = Field("")
    # product_strategist
    product_strategist_name: str = Field("")
    product_strategist_nickname: str = Field("")
    product_strategist_handle: str = Field("")
    product_strategist_personality: str = Field("")
    # pricing_strategist
    pricing_strategist_name: str = Field("")
    pricing_strategist_nickname: str = Field("")
    pricing_strategist_handle: str = Field("")
    pricing_strategist_personality: str = Field("")
    # course_designer
    course_designer_name: str = Field("")
    course_designer_nickname: str = Field("")
    course_designer_handle: str = Field("")
    course_designer_personality: str = Field("")
    # chatbot_designer
    chatbot_designer_name: str = Field("")
    chatbot_designer_nickname: str = Field("")
    chatbot_designer_handle: str = Field("")
    chatbot_designer_personality: str = Field("")
