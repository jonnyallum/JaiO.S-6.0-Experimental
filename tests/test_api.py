"""Test API endpoints — health, catalog, pipelines."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAPIImports:
    def test_fastapi_app_imports(self):
        """API module should import without crashing."""
        from api.main import app
        assert app is not None

    def test_supervisor_imports(self):
        """Supervisor should import cleanly."""
        from graphs.supervisor import run_supervisor, ROUTING_RULES, PIPELINE_TEMPLATES
        assert callable(run_supervisor)
        assert len(ROUTING_RULES) > 50
        assert len(PIPELINE_TEMPLATES) > 15

    def test_tools_import(self):
        """All tools should import cleanly."""
        from tools.web_search import brave_search, search_summary
        from tools.supabase_tools import SupabaseStateLogger
        from tools.code_executor import execute_python, execute_and_summarize
        assert callable(brave_search)
        assert callable(execute_python)
