from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import httpx

from soterra_backend.models import AuthSession, RepositorySnapshot, TenantUser
from soterra_backend.services.insights_agent_service import InsightsAgentService


def _snapshot(findings: list[dict]) -> RepositorySnapshot:
    project_names = sorted({item["project_name"] for item in findings})
    lifecycle_by_project = {
        item["project_name"]: item.get("project_lifecycle", "active")
        for item in findings
    }
    documents = [
        {
            "id": document_id,
            "project_id": f"prj-{index}",
            "project_slug": project.lower().replace(" ", "-"),
            "project_name": project,
            "site_name": project,
            "inspection_type": "Final",
            "trade": "General",
            "report_date": "2026-01-01",
            "status": "Completed",
            "inspector": "Inspector",
            "project_lifecycle": lifecycle_by_project.get(project, "active"),
        }
        for index, (project, document_id) in enumerate(
            [(name, f"rpt-{index}") for index, name in enumerate(project_names)],
            start=1,
        )
    ]
    return RepositorySnapshot(
        projects=[
            {
                "id": f"prj-{index}",
                "tenant_id": findings[0].get("tenant_id", "ten-a") if findings else "ten-a",
                "slug": name.lower().replace(" ", "-"),
                "name": name,
                "site_name": name,
                "project_lifecycle": lifecycle_by_project.get(name, "active"),
            }
            for index, name in enumerate(project_names, start=1)
        ],
        documents=documents,
        jobs=[],
        findings=findings,
        predicted_inspections=[],
    )


def _finding(
    *,
    id: str,
    tenant_id: str = "ten-a",
    title: str = "Missing close-out photo",
    inspection_type: str = "Final",
    project_name: str = "Kauri Apartments",
    project_lifecycle: str = "active",
    document_id: str = "rpt-0",
    severity: str = "High",
) -> dict:
    return {
        "id": id,
        "tenant_id": tenant_id,
        "document_id": document_id,
        "project_slug": project_name.lower().replace(" ", "-"),
        "project_name": project_name,
        "project_lifecycle": project_lifecycle,
        "site_name": project_name,
        "title": title,
        "description": f"{title} needs to be resolved.",
        "severity": severity,
        "trade": "Passive Fire",
        "category": "Evidence gap",
        "location": "Level 2",
        "inspection_type": inspection_type,
        "status": "Open",
        "created_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "reinspections": 0,
        "required_fix": "Provide labelled close-out evidence.",
        "evidence_required": ["Labelled photo"],
        "recurrence_risk": 80,
    }


class FakeInsightsRepository:
    def __init__(self) -> None:
        self.loaded_tenant_ids: list[str] = []
        self.snapshots = {
            "ten-a": _snapshot(
                [
                    _finding(id="a-1"),
                    _finding(id="a-2", title="Fire collar incomplete", inspection_type="Passive Fire"),
                    _finding(id="a-3", title="Missing close-out photo", project_name="Rimu Townhouses"),
                ]
            ),
            "ten-b": _snapshot(
                [
                    _finding(
                        id="b-1",
                        tenant_id="ten-b",
                        title="Other tenant private defect",
                        project_name="Secret Other Tenant",
                    )
                ]
            ),
        }

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        self.loaded_tenant_ids.append(tenant_id)
        return self.snapshots[tenant_id]


class DisabledAgent:
    def status(self) -> dict:
        return {"enabled": False, "configured": False}


class ThrowingAgent:
    def status(self) -> dict:
        return {"enabled": True, "configured": True}

    def generate_inspection_insights(self, *, prompt: str) -> dict:
        raise RuntimeError("model unavailable")


class JsonAgent:
    def __init__(self) -> None:
        self.prompt = ""

    def status(self) -> dict:
        return {"enabled": True, "configured": True}

    def generate_inspection_insights(self, *, prompt: str) -> dict:
        self.prompt = prompt
        return {
            "executiveSummary": ["Check evidence gaps before booking the inspection."],
            "learningInsights": [
                {
                    "title": "Evidence must be ready",
                    "explanation": "Close-out evidence appears repeatedly.",
                    "whyItMatters": "Inspectors need proof to sign off completed work.",
                    "howToAvoid": ["Collect labelled photos before booking."],
                    "relatedTrades": ["Passive Fire"],
                    "relatedInspectionTypes": ["Final"],
                    "severity": "High",
                }
            ],
        }


class DisconnectingRepository:
    def __init__(self) -> None:
        self.calls = 0

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        self.calls += 1
        raise RuntimeError("Server disconnected")


class InsightsAgentServiceTest(unittest.TestCase):
    def test_build_ai_insights_uses_current_tenant_snapshot_and_required_shape(self) -> None:
        repo = FakeInsightsRepository()
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertEqual(repo.loaded_tenant_ids, ["ten-a"])
        self.assertEqual(payload["dataScope"], "tenant")
        self.assertEqual(payload["filter"]["selected"], "All")
        for key in [
            "executiveSummary",
            "currentProjectActions",
            "learningInsights",
            "preInspectionChecklist",
            "historicalLessons",
            "oldProjectLessons",
            "repeatedPatterns",
            "highRiskAreas",
            "rootCauses",
            "suggestedQuestions",
            "suggestedAgentQuestions",
        ]:
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], list)

    def test_active_project_excluded_from_historical_lessons(self) -> None:
        repo = FakeInsightsRepository()
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertTrue(payload["currentProjectActions"])
        self.assertNotIn("Kauri Apartments", str(payload["historicalLessons"]))

    def test_closed_project_included_in_historical_lessons(self) -> None:
        repo = FakeInsightsRepository()
        repo.snapshots["ten-a"] = _snapshot(
            [
                _finding(id="a-1", title="Cavity wrap loose", project_name="Kauri Apartments"),
                _finding(
                    id="h-1",
                    title="Fire collar incomplete",
                    project_name="Closed Rimu",
                    project_lifecycle="closed",
                ),
            ]
        )
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertIn("Closed Rimu", str(payload["historicalLessons"]))
        self.assertNotIn("Kauri Apartments", str(payload["historicalLessons"]))

    def test_one_off_findings_excluded_from_repeated_patterns(self) -> None:
        repo = FakeInsightsRepository()
        repo.snapshots["ten-a"] = _snapshot([_finding(id="a-1", title="One off cavity wrap")])
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertEqual(payload["repeatedPatterns"], [])

    def test_repeated_findings_included_in_repeated_patterns(self) -> None:
        repo = FakeInsightsRepository()
        repo.snapshots["ten-a"] = _snapshot(
            [
                _finding(id="a-1", title="Cavity wrap loose", document_id="rpt-1"),
                _finding(id="a-2", title="Cavity wrap loose", document_id="rpt-2"),
            ]
        )
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertEqual(payload["repeatedPatterns"][0]["issue"], "Cavity wrap loose")
        self.assertGreaterEqual(payload["repeatedPatterns"][0]["occurrenceCount"], 2)

    def test_filtering_by_inspection_type_limits_lessons(self) -> None:
        service = InsightsAgentService(repository=FakeInsightsRepository(), agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="Passive Fire")

        self.assertEqual(payload["filter"]["selected"], "Passive Fire")
        text = str(payload)
        self.assertIn("Fire collar incomplete", text)
        self.assertNotIn("Rimu Townhouses", text)

    def test_fallback_works_when_agent_throws(self) -> None:
        service = InsightsAgentService(repository=FakeInsightsRepository(), agent_service=ThrowingAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertIn("deterministic analytics", payload["confidenceNote"])
        self.assertTrue(payload["preInspectionChecklist"])

    def test_repository_disconnect_returns_valid_response_instead_of_500(self) -> None:
        repo = DisconnectingRepository()
        service = InsightsAgentService(repository=repo, agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertEqual(repo.calls, 2)
        self.assertEqual(payload["dataScope"], "tenant")
        self.assertIn("could not be loaded", payload["confidenceNote"])
        self.assertEqual(payload["learningInsights"], [])

    def test_ai_prompt_contains_only_compact_tenant_scoped_fields(self) -> None:
        agent = JsonAgent()
        service = InsightsAgentService(repository=FakeInsightsRepository(), agent_service=agent)

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertEqual(payload["executiveSummary"], ["Check evidence gaps before booking the inspection."])
        self.assertIn("project_name", agent.prompt)
        self.assertNotIn("source_filename", agent.prompt)
        self.assertNotIn("Secret Other Tenant", agent.prompt)

    def test_no_cross_tenant_data_is_returned(self) -> None:
        service = InsightsAgentService(repository=FakeInsightsRepository(), agent_service=DisabledAgent())

        payload = service.build_ai_insights(tenant_id="ten-a", inspection_type="All")

        self.assertNotIn("Secret Other Tenant", str(payload))
        self.assertNotIn("Other tenant private defect", str(payload))


class FakeAuthRepository(FakeInsightsRepository):
    def __init__(self) -> None:
        super().__init__()
        self.session = AuthSession(
            user=TenantUser(
                id="usr-a",
                tenant_id="ten-a",
                tenant_name="Tenant A",
                name="Admin",
                email="admin@example.test",
                role="admin",
            ),
            access_token="valid-token",
            expires_at="2030-01-01T00:00:00+00:00",
        )

    def initialize(self) -> None:
        return None

    def get_auth_session(self, *, access_token: str) -> AuthSession | None:
        return self.session if access_token == "valid-token" else None


class AiInsightsRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)
        os.environ["SOTERRA_REPOSITORY_MODE"] = "sqlite"
        os.environ["SOTERRA_STORAGE_MODE"] = "local"
        os.environ["SOTERRA_LOCAL_DB_PATH"] = str(root / "test.sqlite3")
        os.environ["SOTERRA_LOCAL_STORAGE_DIR"] = str(root / "storage")
        os.environ["SOTERRA_EXTRACTOR_MODE"] = "demo"
        os.environ["SOTERRA_PROCESS_INLINE"] = "false"
        os.environ["SOTERRA_ENV"] = "test"
        os.environ["SOTERRA_SMTP_HOST"] = ""

        from soterra_backend.api import create_app

        self.app = create_app()
        self.repo = FakeAuthRepository()
        self.app.state.repository = self.repo
        self.app.state.insights_agent_service = InsightsAgentService(repository=self.repo, agent_service=DisabledAgent())

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def test_endpoint_requires_authentication(self) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
            response = await client.get("/dashboard/insights/ai")

        self.assertEqual(response.status_code, 401)

    async def test_endpoint_uses_authenticated_tenant(self) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
            response = await client.get(
                "/dashboard/insights/ai?inspectionType=Passive%20Fire",
                headers={
                    "Authorization": "Bearer valid-token",
                    "X-Soterra-Tenant-Id": "ten-b",
                    "X-Soterra-User-Id": "usr-b",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.repo.loaded_tenant_ids[-1], "ten-a")
        self.assertEqual(response.json()["filter"]["selected"], "Passive Fire")


if __name__ == "__main__":
    unittest.main()
