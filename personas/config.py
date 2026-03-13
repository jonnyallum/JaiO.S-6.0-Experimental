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

    # ── Core Technical ────────────────────────────────────────────────────
    persona_github_intelligence_name: str = Field("")
    persona_github_intelligence_nickname: str = Field("")
    persona_github_intelligence_handle: str = Field("")
    persona_github_intelligence_personality: str = Field("")

    persona_security_audit_name: str = Field("")
    persona_security_audit_nickname: str = Field("")
    persona_security_audit_handle: str = Field("")
    persona_security_audit_personality: str = Field("")

    persona_architecture_review_name: str = Field("")
    persona_architecture_review_nickname: str = Field("")
    persona_architecture_review_handle: str = Field("")
    persona_architecture_review_personality: str = Field("")

    persona_data_extraction_name: str = Field("")
    persona_data_extraction_nickname: str = Field("")
    persona_data_extraction_handle: str = Field("")
    persona_data_extraction_personality: str = Field("")

    persona_quality_validation_name: str = Field("")
    persona_quality_validation_nickname: str = Field("")
    persona_quality_validation_handle: str = Field("")
    persona_quality_validation_personality: str = Field("")

    persona_brief_writer_name: str = Field("")
    persona_brief_writer_nickname: str = Field("")
    persona_brief_writer_handle: str = Field("")
    persona_brief_writer_personality: str = Field("")

    persona_code_reviewer_name: str = Field("")
    persona_code_reviewer_nickname: str = Field("")
    persona_code_reviewer_handle: str = Field("")
    persona_code_reviewer_personality: str = Field("")

    persona_dependency_audit_name: str = Field("")
    persona_dependency_audit_nickname: str = Field("")
    persona_dependency_audit_handle: str = Field("")
    persona_dependency_audit_personality: str = Field("")

    persona_social_post_generator_name: str = Field("")
    persona_social_post_generator_nickname: str = Field("")
    persona_social_post_generator_handle: str = Field("")
    persona_social_post_generator_personality: str = Field("")

    persona_supabase_intelligence_name: str = Field("")
    persona_supabase_intelligence_nickname: str = Field("")
    persona_supabase_intelligence_handle: str = Field("")
    persona_supabase_intelligence_personality: str = Field("")

    # ── Business Intelligence ─────────────────────────────────────────────
    persona_monetisation_strategist_name: str = Field("")
    persona_monetisation_strategist_nickname: str = Field("")
    persona_monetisation_strategist_handle: str = Field("")
    persona_monetisation_strategist_personality: str = Field("")

    persona_sales_conversion_name: str = Field("")
    persona_sales_conversion_nickname: str = Field("")
    persona_sales_conversion_handle: str = Field("")
    persona_sales_conversion_personality: str = Field("")

    persona_content_scaler_name: str = Field("")
    persona_content_scaler_nickname: str = Field("")
    persona_content_scaler_handle: str = Field("")
    persona_content_scaler_personality: str = Field("")

    persona_automation_architect_name: str = Field("")
    persona_automation_architect_nickname: str = Field("")
    persona_automation_architect_handle: str = Field("")
    persona_automation_architect_personality: str = Field("")

    persona_business_intelligence_name: str = Field("")
    persona_business_intelligence_nickname: str = Field("")
    persona_business_intelligence_handle: str = Field("")
    persona_business_intelligence_personality: str = Field("")

    # ── Growth & Intelligence ─────────────────────────────────────────────
    persona_seo_specialist_name: str = Field("")
    persona_seo_specialist_nickname: str = Field("")
    persona_seo_specialist_handle: str = Field("")
    persona_seo_specialist_personality: str = Field("")

    persona_competitor_monitor_name: str = Field("")
    persona_competitor_monitor_nickname: str = Field("")
    persona_competitor_monitor_handle: str = Field("")
    persona_competitor_monitor_personality: str = Field("")

    persona_email_architect_name: str = Field("")
    persona_email_architect_nickname: str = Field("")
    persona_email_architect_handle: str = Field("")
    persona_email_architect_personality: str = Field("")

    persona_video_brief_writer_name: str = Field("")
    persona_video_brief_writer_nickname: str = Field("")
    persona_video_brief_writer_handle: str = Field("")
    persona_video_brief_writer_personality: str = Field("")

    persona_funnel_architect_name: str = Field("")
    persona_funnel_architect_nickname: str = Field("")
    persona_funnel_architect_handle: str = Field("")
    persona_funnel_architect_personality: str = Field("")

    persona_orchestrator_name: str = Field("")
    persona_orchestrator_nickname: str = Field("")
    persona_orchestrator_handle: str = Field("")
    persona_orchestrator_personality: str = Field("")

    # ── Extended Roster ───────────────────────────────────────────────────
    analytics_reporter_name: str = Field("")
    analytics_reporter_nickname: str = Field("")
    analytics_reporter_handle: str = Field("")
    analytics_reporter_personality: str = Field("")

    ad_copy_writer_name: str = Field("")
    ad_copy_writer_nickname: str = Field("")
    ad_copy_writer_handle: str = Field("")
    ad_copy_writer_personality: str = Field("")

    launch_orchestrator_name: str = Field("")
    launch_orchestrator_nickname: str = Field("")
    launch_orchestrator_handle: str = Field("")
    launch_orchestrator_personality: str = Field("")

    case_study_writer_name: str = Field("")
    case_study_writer_nickname: str = Field("")
    case_study_writer_handle: str = Field("")
    case_study_writer_personality: str = Field("")

    knowledge_base_writer_name: str = Field("")
    knowledge_base_writer_nickname: str = Field("")
    knowledge_base_writer_handle: str = Field("")
    knowledge_base_writer_personality: str = Field("")

    agent_builder_name: str = Field("")
    agent_builder_nickname: str = Field("")
    agent_builder_handle: str = Field("")
    agent_builder_personality: str = Field("")

    proposal_writer_name: str = Field("")
    proposal_writer_nickname: str = Field("")
    proposal_writer_handle: str = Field("")
    proposal_writer_personality: str = Field("")

    product_strategist_name: str = Field("")
    product_strategist_nickname: str = Field("")
    product_strategist_handle: str = Field("")
    product_strategist_personality: str = Field("")

    pricing_strategist_name: str = Field("")
    pricing_strategist_nickname: str = Field("")
    pricing_strategist_handle: str = Field("")
    pricing_strategist_personality: str = Field("")

    course_designer_name: str = Field("")
    course_designer_nickname: str = Field("")
    course_designer_handle: str = Field("")
    course_designer_personality: str = Field("")

    chatbot_designer_name: str = Field("")
    chatbot_designer_nickname: str = Field("")
    chatbot_designer_handle: str = Field("")
    chatbot_designer_personality: str = Field("")

    persona_builder_name: str = Field("")
    persona_builder_nickname: str = Field("")
    persona_builder_handle: str = Field("")
    persona_builder_personality: str = Field("")

    # ── Loop 2 agents (awesome-llm-apps + betting) ──
    persona_eval_judge_name: str = Field("")
    persona_eval_judge_nickname: str = Field("")
    persona_eval_judge_handle: str = Field("")
    persona_eval_judge_personality: str = Field("")
    persona_code_executor_name: str = Field("")
    persona_code_executor_nickname: str = Field("")
    persona_code_executor_handle: str = Field("")
    persona_code_executor_personality: str = Field("")
    persona_rag_retriever_name: str = Field("")
    persona_rag_retriever_nickname: str = Field("")
    persona_rag_retriever_handle: str = Field("")
    persona_rag_retriever_personality: str = Field("")
    persona_human_gate_name: str = Field("")
    persona_human_gate_nickname: str = Field("")
    persona_human_gate_handle: str = Field("")
    persona_human_gate_personality: str = Field("")
    persona_workflow_planner_name: str = Field("")
    persona_workflow_planner_nickname: str = Field("")
    persona_workflow_planner_handle: str = Field("")
    persona_workflow_planner_personality: str = Field("")
    persona_summariser_name: str = Field("")
    persona_summariser_nickname: str = Field("")
    persona_summariser_handle: str = Field("")
    persona_summariser_personality: str = Field("")
    persona_translator_name: str = Field("")
    persona_translator_nickname: str = Field("")
    persona_translator_handle: str = Field("")
    persona_translator_personality: str = Field("")
    persona_image_prompt_engineer_name: str = Field("")
    persona_image_prompt_engineer_nickname: str = Field("")
    persona_image_prompt_engineer_handle: str = Field("")
    persona_image_prompt_engineer_personality: str = Field("")
    persona_api_integration_agent_name: str = Field("")
    persona_api_integration_agent_nickname: str = Field("")
    persona_api_integration_agent_handle: str = Field("")
    persona_api_integration_agent_personality: str = Field("")
    persona_risk_analyst_name: str = Field("")
    persona_risk_analyst_nickname: str = Field("")
    persona_risk_analyst_handle: str = Field("")
    persona_risk_analyst_personality: str = Field("")
    persona_onboarding_agent_name: str = Field("")
    persona_onboarding_agent_nickname: str = Field("")
    persona_onboarding_agent_handle: str = Field("")
    persona_onboarding_agent_personality: str = Field("")
    persona_feedback_collector_name: str = Field("")
    persona_feedback_collector_nickname: str = Field("")
    persona_feedback_collector_handle: str = Field("")
    persona_feedback_collector_personality: str = Field("")
    persona_cost_tracker_name: str = Field("")
    persona_cost_tracker_nickname: str = Field("")
    persona_cost_tracker_handle: str = Field("")
    persona_cost_tracker_personality: str = Field("")
    persona_error_recovery_agent_name: str = Field("")
    persona_error_recovery_agent_nickname: str = Field("")
    persona_error_recovery_agent_handle: str = Field("")
    persona_error_recovery_agent_personality: str = Field("")
    persona_betting_systems_name: str = Field("")
    persona_betting_systems_nickname: str = Field("")
    persona_betting_systems_handle: str = Field("")
    persona_betting_systems_personality: str = Field("")
    persona_football_tactical_name: str = Field("")
    persona_football_tactical_nickname: str = Field("")
    persona_football_tactical_handle: str = Field("")
    persona_football_tactical_personality: str = Field("")
    persona_darts_analyst_name: str = Field("")
    persona_darts_analyst_nickname: str = Field("")
    persona_darts_analyst_handle: str = Field("")
    persona_darts_analyst_personality: str = Field("")
    persona_formula1_analyst_name: str = Field("")
    persona_formula1_analyst_nickname: str = Field("")
    persona_formula1_analyst_handle: str = Field("")
    persona_formula1_analyst_personality: str = Field("")
    persona_horse_racing_name: str = Field("")
    persona_horse_racing_nickname: str = Field("")
    persona_horse_racing_handle: str = Field("")
    persona_horse_racing_personality: str = Field("")
    persona_motogp_analyst_name: str = Field("")
    persona_motogp_analyst_nickname: str = Field("")
    persona_motogp_analyst_handle: str = Field("")
    persona_motogp_analyst_personality: str = Field("")
    persona_roulette_math_name: str = Field("")
    persona_roulette_math_nickname: str = Field("")
    persona_roulette_math_handle: str = Field("")
    persona_roulette_math_personality: str = Field("")

    pr_writer_name: str = Field("")
    pr_writer_nickname: str = Field("")
    pr_writer_handle: str = Field("")
    pr_writer_personality: str = Field("")

    ab_test_designer_name: str = Field("")
    ab_test_designer_nickname: str = Field("")
    ab_test_designer_handle: str = Field("")
    ab_test_designer_personality: str = Field("")

    investor_pitch_writer_name: str = Field("")
    investor_pitch_writer_nickname: str = Field("")
    investor_pitch_writer_handle: str = Field("")
    investor_pitch_writer_personality: str = Field("")

    brand_voice_guide_name: str = Field("")
    brand_voice_guide_nickname: str = Field("")
    brand_voice_guide_handle: str = Field("")
    brand_voice_guide_personality: str = Field("")

    # ── Batch 5 ───────────────────────────────────────────────────────────
    ecommerce_strategist_name: str = Field("")
    ecommerce_strategist_nickname: str = Field("")
    ecommerce_strategist_handle: str = Field("")
    ecommerce_strategist_personality: str = Field("")

    data_parser_name: str = Field("")
    data_parser_nickname: str = Field("")
    data_parser_handle: str = Field("")
    data_parser_personality: str = Field("")

    research_analyst_name: str = Field("")
    research_analyst_nickname: str = Field("")
    research_analyst_handle: str = Field("")
    research_analyst_personality: str = Field("")

    pipeline_monitor_name: str = Field("")
    pipeline_monitor_nickname: str = Field("")
    pipeline_monitor_handle: str = Field("")
    pipeline_monitor_personality: str = Field("")

    customer_success_name: str = Field("")
    customer_success_nickname: str = Field("")
    customer_success_handle: str = Field("")
    customer_success_personality: str = Field("")

    # ── Batch 6 ───────────────────────────────────────────────────────────
    truth_verifier_name: str = Field("")
    truth_verifier_nickname: str = Field("")
    truth_verifier_handle: str = Field("")
    truth_verifier_personality: str = Field("")

    content_auditor_name: str = Field("")
    content_auditor_nickname: str = Field("")
    content_auditor_handle: str = Field("")
    content_auditor_personality: str = Field("")

    process_auditor_name: str = Field("")
    process_auditor_nickname: str = Field("")
    process_auditor_handle: str = Field("")
    process_auditor_personality: str = Field("")

    venture_ideator_name: str = Field("")
    venture_ideator_nickname: str = Field("")
    venture_ideator_handle: str = Field("")
    venture_ideator_personality: str = Field("")

    voice_synthesiser_name: str = Field("")
    voice_synthesiser_nickname: str = Field("")
    voice_synthesiser_handle: str = Field("")
    voice_synthesiser_personality: str = Field("")

    # ── Batch 7 ───────────────────────────────────────────────────────────
    fullstack_architect_name: str = Field("")
    fullstack_architect_nickname: str = Field("")
    fullstack_architect_handle: str = Field("")
    fullstack_architect_personality: str = Field("")

    database_architect_name: str = Field("")
    database_architect_nickname: str = Field("")
    database_architect_handle: str = Field("")
    database_architect_personality: str = Field("")

    supabase_specialist_name: str = Field("")
    supabase_specialist_nickname: str = Field("")
    supabase_specialist_handle: str = Field("")
    supabase_specialist_personality: str = Field("")

    devops_engineer_name: str = Field("")
    devops_engineer_nickname: str = Field("")
    devops_engineer_handle: str = Field("")
    devops_engineer_personality: str = Field("")

    deployment_specialist_name: str = Field("")
    deployment_specialist_nickname: str = Field("")
    deployment_specialist_handle: str = Field("")
    deployment_specialist_personality: str = Field("")

    # ── Batch 8 ───────────────────────────────────────────────────────────
    performance_auditor_name: str = Field("")
    performance_auditor_nickname: str = Field("")
    performance_auditor_handle: str = Field("")
    performance_auditor_personality: str = Field("")

    mcp_builder_name: str = Field("")
    mcp_builder_nickname: str = Field("")
    mcp_builder_handle: str = Field("")
    mcp_builder_personality: str = Field("")

    gcp_ai_specialist_name: str = Field("")
    gcp_ai_specialist_nickname: str = Field("")
    gcp_ai_specialist_handle: str = Field("")
    gcp_ai_specialist_personality: str = Field("")

    ui_designer_name: str = Field("")
    ui_designer_nickname: str = Field("")
    ui_designer_handle: str = Field("")
    ui_designer_personality: str = Field("")

    creative_director_name: str = Field("")
    creative_director_nickname: str = Field("")
    creative_director_handle: str = Field("")
    creative_director_personality: str = Field("")

    # ── Batch 9 ───────────────────────────────────────────────────────────
    copywriter_name: str = Field("")
    copywriter_nickname: str = Field("")
    copywriter_handle: str = Field("")
    copywriter_personality: str = Field("")

    project_manager_name: str = Field("")
    project_manager_nickname: str = Field("")
    project_manager_handle: str = Field("")
    project_manager_personality: str = Field("")

    financial_analyst_name: str = Field("")
    financial_analyst_nickname: str = Field("")
    financial_analyst_handle: str = Field("")
    financial_analyst_personality: str = Field("")

    legal_advisor_name: str = Field("")
    legal_advisor_nickname: str = Field("")
    legal_advisor_handle: str = Field("")
    legal_advisor_personality: str = Field("")

    fact_checker_name: str = Field("")
    fact_checker_nickname: str = Field("")
    fact_checker_handle: str = Field("")
    fact_checker_personality: str = Field("")


_persona_settings = PersonaSettings()

# Role slug → env field prefix
# Original 21 use "persona_<role>" prefix; all others use "<role>" prefix directly.
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
    "seo_specialist":          "persona_seo_specialist",
    "competitor_monitor":      "persona_competitor_monitor",
    "email_architect":         "persona_email_architect",
    "video_brief_writer":      "persona_video_brief_writer",
    "funnel_architect":        "persona_funnel_architect",
    # Orchestration
    "orchestrator":            "persona_orchestrator",
    # Extended roster
    "analytics_reporter":      "analytics_reporter",
    "ad_copy_writer":          "ad_copy_writer",
    "launch_orchestrator":     "launch_orchestrator",
    "case_study_writer":       "case_study_writer",
    "knowledge_base_writer":   "knowledge_base_writer",
    "agent_builder":           "agent_builder",
    "proposal_writer":         "proposal_writer",
    "product_strategist":      "product_strategist",
    "pricing_strategist":      "pricing_strategist",
    "course_designer":         "course_designer",
    "chatbot_designer":        "chatbot_designer",
    "persona_builder":         "persona_builder",
    "pr_writer":               "pr_writer",
    "ab_test_designer":        "ab_test_designer",
    "investor_pitch_writer":   "investor_pitch_writer",
    "brand_voice_guide":       "brand_voice_guide",
    # Batch 5
    "ecommerce_strategist":    "ecommerce_strategist",
    "data_parser":             "data_parser",
    "research_analyst":        "research_analyst",
    "pipeline_monitor":        "pipeline_monitor",
    "customer_success":        "customer_success",
    # Batch 6
    "truth_verifier":          "truth_verifier",
    "content_auditor":         "content_auditor",
    "process_auditor":         "process_auditor",
    "venture_ideator":         "venture_ideator",
    "voice_synthesiser":       "voice_synthesiser",
    # Batch 7
    "fullstack_architect":     "fullstack_architect",
    "database_architect":      "database_architect",
    "supabase_specialist":     "supabase_specialist",
    "devops_engineer":         "devops_engineer",
    "deployment_specialist":   "deployment_specialist",
    # Batch 8
    "performance_auditor":     "performance_auditor",
    "mcp_builder":             "mcp_builder",
    "gcp_ai_specialist":       "gcp_ai_specialist",
    "ui_designer":             "ui_designer",
    "creative_director":       "creative_director",
    # Batch 9
    "copywriter":              "copywriter",
    "project_manager":         "project_manager",
    "financial_analyst":       "financial_analyst",
    "legal_advisor":           "legal_advisor",
    "fact_checker":            "fact_checker",
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
    return {role: get_persona(role) for role in _ROLE_MAP}
