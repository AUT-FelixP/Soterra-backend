from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from soterra_backend.agent.schemas import AgentChatResponse, AgentRelatedEntities
from soterra_backend.agent.service import SoterraAgentService
from soterra_backend.agent.prompts import SOTERRA_AGENT_ROUTE_GUIDE
from soterra_backend.agent.tools import ReportDetailTool, TrackerSummaryTool, build_soterra_tools
from soterra_backend.models import RepositorySnapshot


def _find_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parents[2]


def _snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        projects=[
            {"id": "prj-1", "tenant_id": "ten-a", "slug": "project-a", "name": "Project A", "site_name": "Project A", "address": None}
        ],
        documents=[
            {
                "id": "rpt-1",
                "tenant_id": "ten-a",
                "project_id": "prj-1",
                "project_name": "Project A",
                "project_slug": "project-a",
                "site_name": "Project A",
                "address": None,
                "source_filename": "report.pdf",
                "inspection_type": "Waterproofing",
                "trade": "Waterproofing",
                "inspector": "QA Inspector",
                "report_date": "2026-04-20",
                "status": "Reviewing",
                "summary": "Waterproofing defects need follow-up before close-out.",
                "units": [],
                "uploaded_at": "2026-04-20T00:00:00+00:00",
            }
        ],
        jobs=[],
        findings=[
            {
                "id": "issue-1",
                "tenant_id": "ten-a",
                "document_id": "rpt-1",
                "project_id": "prj-1",
                "project_name": "Project A",
                "project_slug": "project-a",
                "site_name": "Project A",
                "inspection_type": "Waterproofing",
                "document_status": "Reviewing",
                "title": "Membrane lap incomplete",
                "description": "Several membrane laps need correction.",
                "category": "Waterproofing",
                "trade": "Waterproofing",
                "severity": "High",
                "status": "Open",
                "location": "Level 2",
                "unit_label": "Unit 201",
                "recurrence_risk": 80,
                "reinspections": 1,
                "last_sent_to": "qa@example.com",
                "created_at": "2026-04-20T00:00:00+00:00",
                "closed_at": None,
            }
        ],
        predicted_inspections=[
            {
                "id": "pred-1",
                "tenant_id": "ten-a",
                "project_id": "prj-1",
                "project_name": "Project A",
                "project_slug": "project-a",
                "inspection_type": "Waterproofing",
                "site_name": "Project A",
                "expected_date": "2026-06-01",
                "risk_level": "High",
                "source": "Repeated waterproofing issues",
                "created_at": "2026-04-20T00:00:00+00:00",
            }
        ],
    )


class FakeRepository:
    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        if tenant_id == "ten-a":
            return _snapshot()
        return RepositorySnapshot(projects=[], documents=[], jobs=[], findings=[], predicted_inspections=[])


class OrphanPredictionRepository:
    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        return RepositorySnapshot(
            projects=[
                {
                    "id": "prj-orphan",
                    "tenant_id": tenant_id,
                    "slug": "24-kauri-road-henderson",
                    "name": "24 Kauri Road, Henderson",
                    "site_name": "24 Kauri Road, Henderson",
                }
            ],
            documents=[],
            jobs=[],
            findings=[],
            predicted_inspections=[
                {
                    "id": "pred-orphan",
                    "tenant_id": tenant_id,
                    "project_id": "prj-orphan",
                    "project_name": "24 Kauri Road, Henderson",
                    "project_slug": "24-kauri-road-henderson",
                    "inspection_type": "Council Recheck",
                    "site_name": "24 Kauri Road, Henderson",
                    "expected_date": "2026-05-18",
                    "risk_level": "High",
                    "source": "Deleted report prediction",
                    "created_at": "2026-05-01T00:00:00+00:00",
                }
            ],
        )


class AgentToolsTest(unittest.TestCase):
    def test_agent_tool_catalog_covers_protected_read_routes(self) -> None:
        tool_names = {tool.name for tool in build_soterra_tools(FakeRepository(), "ten-a")}
        expected_tools = {
            "get_api_route_catalog",
            "get_reports_summary",
            "get_report_detail",
            "get_tracker_summary",
            "get_issue_detail",
            "get_dashboard_summary",
            "get_company_metrics",
            "get_performance_metrics",
            "get_insights",
            "get_project_metrics",
            "get_dashboard_risk",
            "get_live_tracker",
            "get_top_failures",
            "get_upcoming_risk",
            "get_insights_preview",
            "get_inspection_risk",
        }
        self.assertTrue(expected_tools.issubset(tool_names), sorted(expected_tools - tool_names))

    def test_route_guide_maps_backend_routes_to_tools(self) -> None:
        expected_routes = [
            "GET /reports",
            "GET /reports/{report_id}",
            "GET /issues",
            "GET /tracker",
            "GET /dashboard",
            "GET /dashboard/company",
            "GET /dashboard/performance",
            "GET /dashboard/insights",
            "GET /dashboard/project/{slug}",
            "GET /dashboard/risk",
            "GET /dashboard/live-tracker",
            "GET /dashboard/top-failures",
            "GET /dashboard/upcoming-risk",
            "GET /dashboard/insights-preview",
            "GET /inspection-risk",
            "GET /insights",
        ]
        for route in expected_routes:
            self.assertIn(route, SOTERRA_AGENT_ROUTE_GUIDE)

    def test_read_route_tools_return_json_serializable_payloads(self) -> None:
        tools = {tool.name: tool for tool in build_soterra_tools(FakeRepository(), "ten-a")}
        calls = {
            "get_api_route_catalog": ("ten-a",),
            "get_reports_summary": ("ten-a", 10),
            "get_report_detail": ("ten-a", "rpt-1"),
            "get_tracker_summary": ("ten-a",),
            "get_issue_detail": ("ten-a", "issue-1"),
            "get_dashboard_summary": ("ten-a",),
            "get_company_metrics": ("ten-a",),
            "get_performance_metrics": ("ten-a", "All types"),
            "get_project_metrics": ("ten-a", "project-a"),
            "get_dashboard_risk": ("ten-a",),
            "get_live_tracker": ("ten-a",),
            "get_top_failures": ("ten-a",),
            "get_upcoming_risk": ("ten-a",),
            "get_insights_preview": ("ten-a",),
            "get_inspection_risk": ("ten-a", None, None, None),
            "get_insights": ("ten-a",),
        }
        for name, args in calls.items():
            payload = tools[name].forward(*args)
            self.assertIsInstance(payload, dict, name)

    def test_risk_tools_do_not_surface_orphan_predictions_without_reports(self) -> None:
        tools = {tool.name: tool for tool in build_soterra_tools(OrphanPredictionRepository(), "ten-a")}

        dashboard_risk = tools["get_dashboard_risk"].forward("ten-a")
        upcoming_risk = tools["get_upcoming_risk"].forward("ten-a")
        inspection_risk = tools["get_inspection_risk"].forward("ten-a", None, None, None)

        self.assertEqual(dashboard_risk["inspections"], [])
        self.assertEqual(dashboard_risk["likelyFailures"], [])
        self.assertEqual(upcoming_risk["title"], "No upcoming inspections")
        self.assertEqual(upcoming_risk["likelyFailures"], [])
        self.assertEqual(inspection_risk["upcomingInspections"], [])

    def test_get_report_detail_returns_not_found_for_other_tenant(self) -> None:
        used: list[str] = []
        tool = ReportDetailTool(FakeRepository(), "ten-a", used.append)
        payload = tool.forward("ten-b", "rpt-1")
        self.assertEqual(payload["found"], False)
        self.assertEqual(used, ["get_report_detail"])

    def test_get_tracker_summary_returns_compact_issue_summary(self) -> None:
        used: list[str] = []
        tool = TrackerSummaryTool(FakeRepository(), "ten-a", used.append)
        payload = tool.forward("ten-a")
        self.assertEqual(payload["openIssueCount"], 1)
        self.assertEqual(payload["highPriorityIssueCount"], 1)
        self.assertEqual(payload["issues"][0]["id"], "issue-1")
        self.assertNotIn("description", payload["issues"][0])
        self.assertEqual(used, ["get_tracker_summary"])


class AgentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["SOTERRA_AGENT_ENABLED"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("SOTERRA_AGENT_ENABLED", None)
        os.environ.pop("SOTERRA_AGENT_MAX_STEPS", None)
        os.environ.pop("SOTERRA_AGENT_MODEL_PROVIDER", None)
        os.environ.pop("SOTERRA_AGENT_MODEL_ID", None)
        os.environ.pop("SOTERRA_AGENT_HF_PROVIDER", None)

    def test_build_agent_uses_tool_calling_agent_without_base_tools(self) -> None:
        from smolagents import Model, ToolCallingAgent

        class FakeModel(Model):
            def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
                raise AssertionError("This test only checks initialization.")

        service = SoterraAgentService(FakeRepository())
        os.environ["SOTERRA_AGENT_MAX_STEPS"] = "4"
        with patch.object(service, "_build_model", lambda: FakeModel()):
            agent = service._build_agent([])

        self.assertIsInstance(agent, ToolCallingAgent)
        self.assertEqual(agent.max_steps, 4)
        self.assertNotIn("python_interpreter", agent.tools)

    def test_status_reports_disabled_or_missing_model_config_without_secret_values(self) -> None:
        original_api_key = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["SOTERRA_AGENT_ENABLED"] = "false"
        os.environ["SOTERRA_AGENT_MODEL_PROVIDER"] = "openai"

        try:
            status = SoterraAgentService(FakeRepository()).status()
        finally:
            if original_api_key is not None:
                os.environ["OPENAI_API_KEY"] = original_api_key

        self.assertEqual(status["enabled"], False)
        self.assertEqual(status["configured"], False)
        self.assertEqual(status["provider"], "openai")
        self.assertNotIn("api", "".join(status.keys()).lower())

    def test_hugging_face_is_default_agent_provider(self) -> None:
        original_token = os.environ.pop("HF_TOKEN", None)
        original_hub_token = os.environ.pop("HUGGINGFACEHUB_API_TOKEN", None)
        try:
            status = SoterraAgentService(FakeRepository()).status()
        finally:
            if original_token is not None:
                os.environ["HF_TOKEN"] = original_token
            if original_hub_token is not None:
                os.environ["HUGGINGFACEHUB_API_TOKEN"] = original_hub_token

        self.assertEqual(status["provider"], "huggingface")
        self.assertEqual(status["model_id"], "Qwen/Qwen2.5-72B-Instruct")
        self.assertEqual(status["configured"], False)

    def test_hugging_face_model_builds_from_hf_token(self) -> None:
        from smolagents import InferenceClientModel

        os.environ["HF_TOKEN"] = "hf_test_token"
        os.environ["SOTERRA_AGENT_MODEL_PROVIDER"] = "huggingface"
        os.environ["SOTERRA_AGENT_MODEL_ID"] = "Qwen/Qwen2.5-72B-Instruct"

        model = SoterraAgentService(FakeRepository())._build_model()

        self.assertIsInstance(model, InferenceClientModel)
        self.assertEqual(model.model_id, "Qwen/Qwen2.5-72B-Instruct")
        os.environ.pop("HF_TOKEN", None)

    def test_dashboard_question_calls_dashboard_risk_and_failure_tools(self) -> None:
        class FakeAgent:
            def __init__(self, tools):
                self.tools = {tool.name: tool for tool in tools}

            def run(self, task: str) -> str:
                self.tools["get_dashboard_risk"].forward("ten-a")
                self.tools["get_upcoming_risk"].forward("ten-a")
                self.tools["get_top_failures"].forward("ten-a")
                return "Project A is the riskiest because waterproofing issues are recurring. Next action: check open items before the next inspection."

        service = SoterraAgentService(FakeRepository())
        with patch.object(service, "_build_agent", lambda tools: FakeAgent(tools)):
            response = service.chat(message="Which project is most risky right now?", tenant_id="ten-a", user_id="usr-1", role="admin", page_context="dashboard")

        self.assertEqual(response.confidence, "medium")
        self.assertEqual(response.used_tools, ["get_dashboard_risk", "get_upcoming_risk", "get_top_failures"])

    def test_report_question_calls_report_detail_tool(self) -> None:
        class FakeAgent:
            def __init__(self, tools):
                self.tools = {tool.name: tool for tool in tools}

            def run(self, task: str) -> str:
                self.tools["get_report_detail"].forward("ten-a", "rpt-1")
                return "The main report risk is an open high-severity waterproofing defect. Next action: close out the membrane lap corrections."

        service = SoterraAgentService(FakeRepository())
        with patch.object(service, "_build_agent", lambda tools: FakeAgent(tools)):
            response = service.chat(message="What are the main risks in this report?", tenant_id="ten-a", user_id="usr-1", role="admin", report_id="rpt-1")

        self.assertEqual(response.confidence, "high")
        self.assertEqual(response.used_tools, ["get_report_detail"])
        self.assertEqual(response.related_entities.reports, ["rpt-1"])

    def test_missing_data_produces_clear_answer(self) -> None:
        class BrokenAgent:
            def run(self, task: str) -> str:
                raise RuntimeError("model failed")

        service = SoterraAgentService(FakeRepository())
        with patch.object(service, "_build_agent", lambda tools: BrokenAgent()):
            response = service.chat(message="Tell me everything", tenant_id="ten-a", user_id="usr-1", role="admin")

        self.assertIn("could not complete", response.answer.lower())
        self.assertEqual(response.confidence, "low")


class AgentRouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        repo_root = _find_project_root()
        tmp_root = repo_root / "artifacts" / "backend" / "agent-route-test-runtime"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.db_path = tmp_root / "soterra-agent-test.sqlite3"
        self.db_path.unlink(missing_ok=True)
        os.environ["SOTERRA_REPOSITORY_MODE"] = "sqlite"
        os.environ["SOTERRA_STORAGE_MODE"] = "local"
        os.environ["SOTERRA_LOCAL_DB_PATH"] = str(self.db_path)
        os.environ["SOTERRA_LOCAL_STORAGE_DIR"] = str(tmp_root / "storage")
        os.environ["SOTERRA_PROCESS_INLINE"] = "false"
        os.environ["SOTERRA_ENV"] = "test"
        os.environ["SOTERRA_SMTP_HOST"] = ""

        from soterra_backend.api import create_app

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://testserver",
            headers={"accept": "application/json"},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        os.environ.pop("SOTERRA_AGENT_ENABLED", None)

    async def _auth_headers(self) -> dict[str, str]:
        response = await self.client.post(
            "/auth/register",
            json={
                "tenantName": "Agent Route Tenant",
                "name": "Agent Admin",
                "email": "agent-route@example.com",
                "password": "VeryStrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def test_agent_chat_rejects_unauthenticated_requests(self) -> None:
        response = await self.client.post("/agent/chat", json={"message": "Which issues need attention first?"})
        self.assertEqual(response.status_code, 401)

    async def test_agent_chat_returns_503_when_disabled(self) -> None:
        headers = await self._auth_headers()
        os.environ["SOTERRA_AGENT_ENABLED"] = "false"
        response = await self.client.post("/agent/chat", json={"message": "Which issues need attention first?"}, headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn("disabled", response.json()["detail"].lower())

    async def test_agent_chat_does_not_accept_tenant_id_from_body(self) -> None:
        headers = await self._auth_headers()
        os.environ["SOTERRA_AGENT_ENABLED"] = "true"
        response = await self.client.post(
            "/agent/chat",
            json={"message": "Which issues need attention first?", "tenant_id": "ten-other"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 422)

    async def test_agent_chat_uses_authenticated_tenant_context(self) -> None:
        headers = await self._auth_headers()
        os.environ["SOTERRA_AGENT_ENABLED"] = "true"
        test_case = self

        def fake_chat(self, **kwargs):
            test_case.assertNotEqual(kwargs["tenant_id"], "ten-other")
            return AgentChatResponse(
                answer="Use the tracker to close out the high priority open items first.",
                used_tools=["get_tracker_summary"],
                related_entities=AgentRelatedEntities(),
                confidence="medium",
            )

        with patch.object(SoterraAgentService, "chat", fake_chat):
            response = await self.client.post("/agent/chat", json={"message": "Which issues need attention first?"}, headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["used_tools"], ["get_tracker_summary"])
