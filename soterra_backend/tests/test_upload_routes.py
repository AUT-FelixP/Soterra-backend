from __future__ import annotations

from dataclasses import replace
import hashlib
import os
import sqlite3
import unittest
from pathlib import Path

import httpx


def _find_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parents[2]


def _find_fixture_pdf() -> Path:
    repo_root = _find_project_root()
    candidates = list((repo_root / "artifacts" / "backend").rglob("*.pdf"))
    if not candidates:
        raise unittest.SkipTest("No PDF fixtures found under artifacts/backend")
    # Prefer a known benchmark file name so we get stable demo profiles when using SOTERRA_EXTRACTOR_MODE=demo.
    preferred = next((p for p in candidates if "Fire Inspection - 07 Kauri Apartments" in p.name), None)
    return preferred or sorted(candidates)[0]


class UploadAndRoutesTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_root = _find_project_root()
        tmp_root = repo_root / "artifacts" / "backend" / "route-test-runtime"
        tmp_root.mkdir(parents=True, exist_ok=True)

        cls.db_path = tmp_root / "soterra-test.sqlite3"
        cls.storage_dir = tmp_root / "storage"
        cls.db_path.unlink(missing_ok=True)

        os.environ["SOTERRA_REPOSITORY_MODE"] = "sqlite"
        os.environ["SOTERRA_STORAGE_MODE"] = "local"
        os.environ["SOTERRA_LOCAL_DB_PATH"] = str(cls.db_path)
        os.environ["SOTERRA_LOCAL_STORAGE_DIR"] = str(cls.storage_dir)
        os.environ["SOTERRA_PROCESS_INLINE"] = "false"
        os.environ["SOTERRA_ENV"] = "test"
        # Use demo extraction for deterministic, fast tests. This exercises the full ingest->persist->analytics flow
        # without requiring OCR model downloads.
        os.environ["SOTERRA_EXTRACTOR_MODE"] = "demo"
        os.environ["SOTERRA_ALLOW_MODEL_EXTRACTION"] = "false"

        from soterra_backend.api import create_app

        cls.app = create_app()

        cls.fixture_pdf = _find_fixture_pdf()
        cls.fixture_bytes = cls.fixture_pdf.read_bytes()
        cls.fixture_hash = hashlib.sha256(cls.fixture_bytes).hexdigest()
        cls.expected_file_tag = f"file-{cls.fixture_hash[:12]}"
        cls.auth_headers: dict[str, str] | None = None

    @classmethod
    def tearDownClass(cls) -> None:
        return None

    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            headers={"accept": "application/json"},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def _upload_once(self) -> tuple[int, dict]:
        await self._ensure_auth()
        response = await self.client.post(
            "/reports",
            data={
                "project": "Kauri Apartments",
                "site": "Kauri Apartments",
                "status": "Reviewing",
                "inspector": "Test Inspector",
                "trade": "General",
            },
            files={"file": (self.fixture_pdf.name, self.fixture_bytes, "application/pdf")},
            headers=self.auth_headers,
        )
        payload = response.json()
        return response.status_code, payload

    async def _ensure_auth(self) -> None:
        if self.__class__.auth_headers:
            return
        response = await self.client.post(
            "/auth/register",
            json={
                "tenantName": "Route Test Tenant",
                "name": "Route Test Admin",
                "email": "route-test@example.com",
                "password": "VeryStrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        token = response.json().get("access_token")
        self.assertTrue(token)
        self.__class__.auth_headers = {"Authorization": f"Bearer {token}"}

    async def _wait_for_report_issues(self, report_id: str, timeout_seconds: int = 30) -> dict:
        import asyncio
        import time

        started = time.monotonic()
        while True:
            detail_response = await self.client.get(f"/reports/{report_id}", headers=self.auth_headers)
            payload = detail_response.json()
            issues = payload.get("item", {}).get("issues", [])
            if issues:
                return payload
            if time.monotonic() - started > timeout_seconds:
                raise AssertionError(f"Timed out waiting for extraction for report {report_id}")
            await asyncio.sleep(0.1)

    def _connect_db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    async def test_upload_dedup_and_views(self) -> None:
        status_code, payload = await self._upload_once()
        self.assertEqual(status_code, 202, payload)
        self.assertIs(payload.get("isDuplicate"), False)
        self.assertIn("item", payload)
        report = payload["item"]
        report_id = report["id"]
        self.assertTrue(report_id.startswith("rpt-"))

        detail = await self._wait_for_report_issues(report_id)
        report = detail["item"]

        # Upload the same bytes again; backend should not create duplicate DB rows.
        status_code_2, payload_2 = await self._upload_once()
        self.assertEqual(status_code_2, 200, payload_2)
        self.assertIs(payload_2.get("isDuplicate"), True)
        self.assertEqual(payload_2["item"]["id"], report_id)

        connection = self._connect_db()
        try:
            docs = connection.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
            findings = connection.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"]
            self.assertEqual(docs, 1)
            self.assertGreater(findings, 0)

            document = connection.execute(
                "SELECT file_hash, file_tag, source_filename FROM documents LIMIT 1"
            ).fetchone()
            self.assertEqual(document["file_hash"], self.fixture_hash)
            self.assertEqual(document["file_tag"], self.expected_file_tag)
            self.assertEqual(document["source_filename"], self.fixture_pdf.name)

            # The analytics views should reflect the extracted findings without a manual refresh.
            summary_count = connection.execute(
                "SELECT COUNT(*) AS c FROM analytics_report_summary_v"
            ).fetchone()["c"]
            company_row = connection.execute("SELECT * FROM analytics_company_metrics_v").fetchone()
            self.assertEqual(summary_count, 1)
            self.assertIsNotNone(company_row)
            self.assertEqual(company_row["total_reports"], 1)
            self.assertGreater(company_row["total_findings"], 0)
        finally:
            connection.close()

        # Validate the main read routes return data for the newly uploaded report.
        reports = (await self.client.get("/reports", headers=self.auth_headers)).json()
        items = reports.get("items") if isinstance(reports, dict) else reports
        self.assertTrue(any(item.get("id") == report_id for item in (items or [])))

        self.assertIn("item", detail)
        self.assertEqual(detail["item"]["id"], report_id)
        self.assertGreaterEqual(len(detail["item"].get("issues") or []), 1)

        issues = (await self.client.get("/issues", headers=self.auth_headers)).json()
        issue_items = issues.get("items") if isinstance(issues, dict) else issues
        self.assertTrue(issue_items)
        issue_id = issue_items[0]["id"]

        issue_detail = (await self.client.get(f"/issues/{issue_id}", headers=self.auth_headers)).json()
        self.assertEqual(issue_detail["item"]["id"], issue_id)

        # Patch issue through both routes (issues + tracker) to ensure writes succeed.
        patched = (
            await self.client.patch(
            f"/issues/{issue_id}", json={"status": "Ready", "reinspections": 2, "lastSentTo": "qa@example.com"}, headers=self.auth_headers
            )
        ).json()
        self.assertEqual(patched["item"]["status"], "Ready")

        patched_tracker = (await self.client.patch(f"/tracker/{issue_id}", json={"status": "Closed", "reinspections": 3}, headers=self.auth_headers)).json()
        self.assertEqual(patched_tracker["item"]["status"], "Closed")

        # Exercise the analytics endpoints that feed the frontend.
        self.assertEqual((await self.client.get("/dashboard", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/company", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/performance", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/insights", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/live-tracker", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/top-failures", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/upcoming-risk", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/dashboard/insights-preview", headers=self.auth_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/inspection-risk", headers=self.auth_headers)).status_code, 200)

        tracker = (await self.client.get("/tracker", headers=self.auth_headers)).json()
        tracker_items = (tracker or {}).get("issueRegister", {}).get("items", [])
        self.assertTrue(tracker_items)
        self.assertEqual((await self.client.get(f"/tracker/{issue_id}", headers=self.auth_headers)).status_code, 200)

        # Project dashboards use slug. Slug comes from project name (lowercased, spaces -> dashes).
        self.assertEqual((await self.client.get("/dashboard/project/kauri-apartments", headers=self.auth_headers)).status_code, 200)

    async def test_model_extraction_disabled(self) -> None:
        health = (await self.client.get("/health")).json()
        self.assertEqual(health, {"status": "ok"})

        # The backend should refuse to instantiate a model extractor when the flag is disabled.
        from soterra_backend.config import Settings
        from soterra_backend.extractors import build_extractor

        settings = replace(Settings.from_env(), extractor_mode="openai", allow_model_extraction=False)
        with self.assertRaises(RuntimeError):
            _ = build_extractor(settings)
