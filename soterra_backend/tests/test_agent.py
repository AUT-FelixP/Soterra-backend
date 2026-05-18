from __future__ import annotations

import unittest

from soterra_backend.agent.service import SoterraAgentService
from soterra_backend.agent.tools import build_soterra_tools
from soterra_backend.models import RepositorySnapshot


class FakeRepository:
    def __init__(self) -> None:
        self.snapshot = RepositorySnapshot(
            projects=[
                {
                    "id": "prj-1",
                    "tenant_id": "ten-1",
                    "slug": "kauri-apartments",
                    "name": "Kauri Apartments",
                    "site_name": "Kauri Apartments",
                    "address": "1 Kauri Road",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": "prj-2",
                    "tenant_id": "ten-1",
                    "slug": "rimu-townhouses",
                    "name": "Rimu Townhouses",
                    "site_name": "Rimu Townhouses",
                    "address": "8 Rimu Lane",
                    "created_at": "2026-01-03T00:00:00+00:00",
                }
            ],
            documents=[
                {
                    "id": "rpt-1",
                    "tenant_id": "ten-1",
                    "project_id": "prj-1",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Kauri Apartments",
                    "source_filename": "inspection.pdf",
                    "inspection_type": "Fire",
                    "trade": "Fire",
                    "inspector": "Inspector",
                    "report_date": "2026-01-02",
                    "status": "Completed",
                    "summary": "Fire stopping issues found.",
                    "units": ["Level 1"],
                    "uploaded_at": "2026-01-02T00:00:00+00:00",
                },
                {
                    "id": "rpt-2",
                    "tenant_id": "ten-1",
                    "project_id": "prj-2",
                    "project_name": "Rimu Townhouses",
                    "project_slug": "rimu-townhouses",
                    "site_name": "Rimu Townhouses",
                    "source_filename": "handover.pdf",
                    "inspection_type": "Handover",
                    "trade": "General",
                    "inspector": "Inspector",
                    "report_date": "2026-01-05",
                    "status": "Completed",
                    "summary": "Handover items closed.",
                    "units": ["Block A"],
                    "uploaded_at": "2026-01-05T00:00:00+00:00",
                }
            ],
            jobs=[
                {
                    "id": "job-1",
                    "document_id": "rpt-1",
                    "status": "completed",
                    "extractor": "demo",
                    "error_message": None,
                    "raw_text_excerpt": "Safe report text excerpt.",
                    "started_at": "2026-01-02T00:00:00+00:00",
                    "completed_at": "2026-01-02T00:01:00+00:00",
                },
                {
                    "id": "job-2",
                    "document_id": "rpt-2",
                    "status": "completed",
                    "extractor": "demo",
                    "error_message": None,
                    "raw_text_excerpt": "Handover passed with close-out evidence.",
                    "started_at": "2026-01-05T00:00:00+00:00",
                    "completed_at": "2026-01-05T00:01:00+00:00",
                }
            ],
            findings=[
                {
                    "id": "issue-1",
                    "tenant_id": "ten-1",
                    "document_id": "rpt-1",
                    "project_id": "prj-1",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Kauri Apartments",
                    "title": "Fire collar missing",
                    "description": "Fire collar missing in riser.",
                    "category": "Fire stopping",
                    "trade": "Fire",
                    "severity": "High",
                    "status": "Open",
                    "location": "Riser",
                    "unit_label": "Level 1",
                    "recurrence_risk": 80,
                    "reinspections": 0,
                    "last_sent_to": None,
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "closed_at": None,
                    "inspection_type": "Fire",
                    "document_status": "Completed",
                },
                {
                    "id": "issue-2",
                    "tenant_id": "ten-1",
                    "document_id": "rpt-1",
                    "project_id": "prj-1",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Kauri Apartments",
                    "title": "Fire collar missing",
                    "description": "Repeat fire collar issue in riser.",
                    "category": "Fire stopping",
                    "trade": "Fire",
                    "severity": "Critical",
                    "status": "Open",
                    "location": "Riser",
                    "unit_label": "Level 2",
                    "recurrence_risk": 95,
                    "reinspections": 2,
                    "last_sent_to": None,
                    "created_at": "2026-01-03T00:00:00+00:00",
                    "closed_at": None,
                    "inspection_type": "Fire",
                    "document_status": "Completed",
                },
                {
                    "id": "issue-3",
                    "tenant_id": "ten-1",
                    "document_id": "rpt-2",
                    "project_id": "prj-2",
                    "project_name": "Rimu Townhouses",
                    "project_slug": "rimu-townhouses",
                    "site_name": "Rimu Townhouses",
                    "title": "Sealant touch-up complete",
                    "description": "Sealant item closed with evidence.",
                    "category": "Waterproofing",
                    "trade": "Sealant",
                    "severity": "Low",
                    "status": "Closed",
                    "location": "Bathroom",
                    "unit_label": "Block A",
                    "recurrence_risk": 20,
                    "reinspections": 0,
                    "last_sent_to": "qa@example.test",
                    "created_at": "2026-01-04T00:00:00+00:00",
                    "closed_at": "2026-01-06T00:00:00+00:00",
                    "inspection_type": "Handover",
                    "document_status": "Completed",
                }
            ],
            predicted_inspections=[
                {
                    "id": "pred-1",
                    "tenant_id": "ten-1",
                    "project_id": "prj-1",
                    "project_slug": "kauri-apartments",
                    "project_name": "Kauri Apartments",
                    "inspection_type": "Fire",
                    "site_name": "Kauri Apartments",
                    "expected_date": "2026-02-01",
                    "risk_level": "High",
                    "source": "demo",
                    "created_at": "2026-01-02T00:00:00+00:00",
                },
                {
                    "id": "pred-2",
                    "tenant_id": "ten-1",
                    "project_id": "prj-2",
                    "project_slug": "rimu-townhouses",
                    "project_name": "Rimu Townhouses",
                    "inspection_type": "Handover",
                    "site_name": "Rimu Townhouses",
                    "expected_date": "2026-02-03",
                    "risk_level": "Low",
                    "source": "demo",
                    "created_at": "2026-01-05T00:00:00+00:00",
                }
            ],
        )

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        self.last_tenant_id = tenant_id
        return self.snapshot

    def list_members(self, *, tenant_id: str) -> list[dict]:
        return [
            {
                "id": "usr-1",
                "tenant_id": tenant_id,
                "name": "Admin User",
                "email": "admin@example.test",
                "role": "admin",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "usr-2",
                "tenant_id": tenant_id,
                "name": "Member User",
                "email": "member@example.test",
                "role": "member",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]


class AgentToolCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.used_tools: list[str] = []
        self.tools = {tool.name: tool for tool in build_soterra_tools(FakeRepository(), "ten-1", self.used_tools.append)}

    def test_agent_has_complete_backend_schema_context(self) -> None:
        catalog = self.tools["get_data_schema_catalog"].forward("ten-1")
        table_names = {item["table"] for item in catalog["tables"]}
        self.assertEqual(
            table_names,
            {
                "tenants",
                "users",
                "auth_sessions",
                "password_reset_tokens",
                "projects",
                "documents",
                "jobs",
                "findings",
                "predicted_inspections",
            },
        )
        self.assertEqual(len(catalog["analyticsViews"]), 5)
        self.assertIn("get_data_schema_catalog", self.used_tools)

    def test_agent_catalog_covers_non_analytics_read_routes(self) -> None:
        route_catalog = self.tools["get_api_route_catalog"].forward("ten-1")
        routes = {item["route"] for item in route_catalog["routes"]}
        self.assertIn("GET /tenants/members", routes)
        self.assertIn("GET /auth/session", routes)
        self.assertIn("GET /agent/chat/status", routes)
        self.assertIn("GET /health", routes)

    def test_member_project_and_job_tools_return_safe_data(self) -> None:
        members = self.tools["get_tenant_members"].forward("ten-1")
        self.assertEqual(members["count"], 2)
        self.assertNotIn("password_hash", members["items"][0])

        projects = self.tools["get_project_catalog"].forward("ten-1")
        self.assertEqual(projects["items"][0]["slug"], "kauri-apartments")
        self.assertEqual(projects["items"][0]["reportCount"], 1)

        jobs = self.tools["get_ingestion_jobs"].forward("ten-1")
        self.assertEqual(jobs["items"][0]["status"], "completed")
        self.assertNotIn("raw_payload_json", jobs["items"][0])

    def test_construction_prompts_route_to_analyst_data(self) -> None:
        service = SoterraAgentService(FakeRepository())
        cases = [
            ("What are the repitative issues which are the root cause for reinspection?", "get_issue_analytics", "Repeat issues"),
            ("What are the open issues that we need to fix this week?", "get_issue_analytics", "Open issues"),
            ("Which site has the highest issues? any insight on the causes", "get_issue_analytics", "highest issue count"),
            ("Which project issues have been closed", "get_issue_analytics", "Recently closed"),
            ("Which sites have passed the inspection", "get_issue_analytics", "Sites that look passed"),
            ("Which category of issues we have the most? for example fire etc", "get_issue_analytics", "biggest issue category"),
        ]
        for prompt, expected_tool, expected_text in cases:
            with self.subTest(prompt=prompt):
                used_tools: list[str] = []
                answer = service._fallback_answer(
                    message=prompt,
                    tenant_id="ten-1",
                    report_id=None,
                    issue_id=None,
                    project_slug=None,
                    page_context=None,
                    used_tools=used_tools,
                )
                self.assertIn(expected_tool, used_tools)
                self.assertIn(expected_text, answer)

    def test_inspection_and_off_domain_prompts_have_clear_defaults(self) -> None:
        service = SoterraAgentService(FakeRepository())

        used_tools: list[str] = []
        inspection_answer = service._fallback_answer(
            message="When is the next inspection due for all the properties we are working on and what issues need to be fixed on high priority?",
            tenant_id="ten-1",
            report_id=None,
            issue_id=None,
            project_slug=None,
            page_context=None,
            used_tools=used_tools,
        )
        self.assertIn("get_inspection_risk", used_tools)
        self.assertIn("Upcoming inspections across properties", inspection_answer)
        self.assertIn("High-priority issues", inspection_answer)

        used_tools = []
        off_domain_answer = service._fallback_answer(
            message="When is the All Blacks match?",
            tenant_id="ten-1",
            report_id=None,
            issue_id=None,
            project_slug=None,
            page_context=None,
            used_tools=used_tools,
        )
        self.assertEqual(used_tools, [])
        self.assertIn("only answer from Soterra construction data", off_domain_answer)


if __name__ == "__main__":
    unittest.main()
