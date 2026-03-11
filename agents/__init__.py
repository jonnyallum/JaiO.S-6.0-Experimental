"""
Agentic skill modules — role-based, persona-independent.
Persona injection (name, nickname, personality) is handled by personas/config.py.
"""

# ── Batch 1: Core Technical ────────────────────────────────────────────────────────────
from agents.github_intelligence   import github_intelligence_node,   GitHubIntelState
from agents.security_audit        import security_audit_node,        SecurityAuditState
from agents.architecture_review   import architecture_review_node,   ArchitectureReviewState
from agents.data_extraction       import data_extraction_node,       DataExtractionState
from agents.quality_validation    import quality_validation_node,    QualityValidationState
from agents.code_reviewer         import code_reviewer_node,         CodeReviewState
from agents.dependency_audit      import dependency_audit_node,      DependencyAuditState
from agents.fullstack_architect   import fullstack_architect_node,   FullstackArchitectState
from agents.database_architect    import database_architect_node,    DatabaseArchitectState
from agents.supabase_specialist   import supabase_specialist_node,   SupabaseSpecialistState
from agents.devops_engineer       import devops_engineer_node,       DevOpsEngineerState
from agents.deployment_specialist import deployment_specialist_node, DeploymentSpecialistState
from agents.performance_auditor   import performance_auditor_node,   PerformanceAuditorState
from agents.mcp_builder           import mcp_builder_node,           McpBuilderState
from agents.gcp_ai_specialist     import gcp_ai_specialist_node,     GcpAiSpecialistState
from agents.data_parser           import data_parser_node,           DataParserState
from agents.agent_builder         import agent_builder_node,         AgentBuilderState
from agents.pipeline_monitor      import pipeline_monitor_node,      PipelineMonitorState
from agents.process_auditor       import process_auditor_node,       ProcessAuditorState
from agents.truth_verifier        import truth_verifier_node,        TruthVerifierState

# ── Batch 2: Content & Creative ─────────────────────────────────────────────────────
from agents.social_post_generator import social_post_generator_node, SocialPostState
from agents.content_scaler        import content_scaler_node,        ContentScalerState
from agents.ad_copy_writer        import ad_copy_writer_node,        AdCopyState
from agents.copywriter            import copywriter_node,            CopywriterState
from agents.brand_voice_guide     import brand_voice_guide_node,     BrandVoiceState
from agents.creative_director     import creative_director_node,     CreativeDirectorState
from agents.video_brief_writer    import video_brief_writer_node,    VideoBriefState
from agents.voice_synthesiser     import voice_synthesiser_node,     VoiceSynthesiserState
from agents.ui_designer           import ui_designer_node,           UiDesignerState
from agents.content_auditor       import content_auditor_node,       ContentAuditorState
from agents.pr_writer             import pr_writer_node,             PRState

# ── Batch 3: Business & Strategy ────────────────────────────────────────────────────
from agents.brief_writer           import brief_writer_node,           BriefWriterState
from agents.proposal_writer        import proposal_writer_node,        ProposalState
from agents.monetisation_strategist import monetisation_strategist_node, MonetisationState
from agents.pricing_strategist     import pricing_strategist_node,     PricingState
from agents.product_strategist     import product_strategist_node,     ProductStrategyState
from agents.sales_conversion       import sales_conversion_node,       SalesConversionState
from agents.funnel_architect       import funnel_architect_node,       FunnelState
from agents.ecommerce_strategist   import ecommerce_strategist_node,   EcommerceStrategistState
from agents.launch_orchestrator    import launch_orchestrator_node,    LaunchState
from agents.venture_ideator        import venture_ideator_node,        VentureIdeatorState
from agents.investor_pitch_writer  import investor_pitch_writer_node,  InvestorPitchState

# ── Batch 4: Intelligence & Research ───────────────────────────────────────────────
from agents.business_intelligence import business_intelligence_node, BIReportState
from agents.analytics_reporter    import analytics_reporter_node,    AnalyticsState
from agents.research_analyst      import research_analyst_node,      ResearchAnalystState
from agents.competitor_monitor    import competitor_monitor_node,    CompetitorIntelState
from agents.seo_specialist        import seo_specialist_node,        SEOState
from agents.supabase_intelligence import supabase_intelligence_node, BrainIntelState
from agents.fact_checker          import fact_checker_node,          FactCheckerState

# ── Batch 5: Operations & Delivery ─────────────────────────────────────────────────
from agents.automation_architect  import automation_architect_node,  AutomationState
from agents.email_architect       import email_architect_node,       EmailSequenceState
from agents.project_manager       import project_manager_node,       ProjectManagerState
from agents.customer_success      import customer_success_node,      CustomerSuccessState
from agents.knowledge_base_writer import knowledge_base_writer_node, KnowledgeBaseState
from agents.case_study_writer     import case_study_writer_node,     CaseStudyState
from agents.course_designer       import course_designer_node,       CourseState
from agents.chatbot_designer      import chatbot_designer_node,      ChatbotState
from agents.persona_builder       import persona_builder_node,       PersonaState
from agents.financial_analyst     import financial_analyst_node,     FinancialAnalystState
from agents.legal_advisor         import legal_advisor_node,         LegalAdvisorState
from agents.ab_test_designer      import ab_test_designer_node,      ABTestState


__all__ = [
    # ── Batch 1: Core Technical
    "github_intelligence_node",   "GitHubIntelState",
    "security_audit_node",        "SecurityAuditState",
    "architecture_review_node",   "ArchitectureReviewState",
    "data_extraction_node",       "DataExtractionState",
    "quality_validation_node",    "QualityValidationState",
    "code_reviewer_node",         "CodeReviewState",
    "dependency_audit_node",      "DependencyAuditState",
    "fullstack_architect_node",   "FullstackArchitectState",
    "database_architect_node",    "DatabaseArchitectState",
    "supabase_specialist_node",   "SupabaseSpecialistState",
    "devops_engineer_node",       "DevOpsEngineerState",
    "deployment_specialist_node", "DeploymentSpecialistState",
    "performance_auditor_node",   "PerformanceAuditorState",
    "mcp_builder_node",           "McpBuilderState",
    "gcp_ai_specialist_node",     "GcpAiSpecialistState",
    "data_parser_node",           "DataParserState",
    "agent_builder_node",         "AgentBuilderState",
    "pipeline_monitor_node",      "PipelineMonitorState",
    "process_auditor_node",       "ProcessAuditorState",
    "truth_verifier_node",        "TruthVerifierState",
    # ── Batch 2: Content & Creative
    "social_post_generator_node", "SocialPostState",
    "content_scaler_node",        "ContentScalerState",
    "ad_copy_writer_node",        "AdCopyState",
    "copywriter_node",            "CopywriterState",
    "brand_voice_guide_node",     "BrandVoiceState",
    "creative_director_node",     "CreativeDirectorState",
    "video_brief_writer_node",    "VideoBriefState",
    "voice_synthesiser_node",     "VoiceSynthesiserState",
    "ui_designer_node",           "UiDesignerState",
    "content_auditor_node",       "ContentAuditorState",
    "pr_writer_node",             "PRState",
    # ── Batch 3: Business & Strategy
    "brief_writer_node",            "BriefWriterState",
    "proposal_writer_node",         "ProposalState",
    "monetisation_strategist_node", "MonetisationState",
    "pricing_strategist_node",      "PricingState",
    "product_strategist_node",      "ProductStrategyState",
    "sales_conversion_node",        "SalesConversionState",
    "funnel_architect_node",        "FunnelState",
    "ecommerce_strategist_node",    "EcommerceStrategistState",
    "launch_orchestrator_node",     "LaunchState",
    "venture_ideator_node",         "VentureIdeatorState",
    "investor_pitch_writer_node",   "InvestorPitchState",
    # ── Batch 4: Intelligence & Research
    "business_intelligence_node",  "BIReportState",
    "analytics_reporter_node",     "AnalyticsState",
    "research_analyst_node",       "ResearchAnalystState",
    "competitor_monitor_node",     "CompetitorIntelState",
    "seo_specialist_node",         "SEOState",
    "supabase_intelligence_node",  "BrainIntelState",
    "fact_checker_node",           "FactCheckerState",
    # ── Batch 5: Operations & Delivery
    "automation_architect_node",  "AutomationState",
    "email_architect_node",       "EmailSequenceState",
    "project_manager_node",       "ProjectManagerState",
    "customer_success_node",      "CustomerSuccessState",
    "knowledge_base_writer_node", "KnowledgeBaseState",
    "case_study_writer_node",     "CaseStudyState",
    "course_designer_node",       "CourseState",
    "chatbot_designer_node",      "ChatbotState",
    "persona_builder_node",       "PersonaState",
    "financial_analyst_node",     "FinancialAnalystState",
    "legal_advisor_node",         "LegalAdvisorState",
    "ab_test_designer_node",      "ABTestState",
]
