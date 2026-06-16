from __future__ import annotations

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


def _blank_pdf_bytes() -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Inspection upload smoke test. The consultant recorded general site observations, attendance notes, "
        "weather conditions, and confirmation that this sample report has enough readable text for storage.",
    )
    return document.tobytes()


def _empty_pdf_bytes() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page()
    return document.tobytes()


class ExtractionDeleteConsistencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        repo_root = _find_project_root()
        tmp_root = repo_root / "artifacts" / "backend" / "extraction-delete-test-runtime"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.db_path = tmp_root / "soterra-extraction-delete.sqlite3"
        self.db_path.unlink(missing_ok=True)

        os.environ["SOTERRA_REPOSITORY_MODE"] = "sqlite"
        os.environ["SOTERRA_STORAGE_MODE"] = "local"
        os.environ["SOTERRA_LOCAL_DB_PATH"] = str(self.db_path)
        os.environ["SOTERRA_LOCAL_STORAGE_DIR"] = str(tmp_root / "storage")
        os.environ["SOTERRA_PROCESS_INLINE"] = "true"
        os.environ["SOTERRA_ENV"] = "test"
        os.environ["SOTERRA_EXTRACTOR_MODE"] = "package"
        os.environ["SOTERRA_PACKAGE_OCR_ENABLED"] = "false"
        os.environ["SOTERRA_SMTP_HOST"] = ""

        from soterra_backend.api import create_app

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://testserver",
            headers={"accept": "application/json"},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        response = await self.client.post(
            "/auth/register",
            json={
                "tenantName": "Extraction Delete Tenant",
                "name": "Extraction Admin",
                "email": "extraction-delete@example.com",
                "password": "VeryStrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def _count_rows(self, table: str) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            connection.close()

    async def test_package_extraction_without_dummy_findings_and_delete_clears_persisted_rows(self) -> None:
        headers = await self._auth_headers()
        upload = await self.client.post(
            "/reports",
            data={
                "project": "Fallback Project",
                "site": "Fallback Site",
                "status": "Reviewing",
                "inspector": "QA",
                "trade": "General",
            },
            files={"file": ("fallback-report.pdf", _blank_pdf_bytes(), "application/pdf")},
            headers=headers,
        )

        self.assertEqual(upload.status_code, 201, upload.text)
        payload = upload.json()
        report_id = payload["item"]["id"]
        self.assertEqual(payload["item"].get("issues") or [], [])
        self.assertEqual(self._count_rows("documents"), 1)
        self.assertEqual(self._count_rows("findings"), 0)
        self.assertEqual(self._count_rows("jobs"), 1)

        dashboard = await self.client.get("/dashboard", headers=headers)
        self.assertEqual(dashboard.status_code, 200)
        metrics = {item["label"]: item["value"] for item in dashboard.json()["metrics"]}
        self.assertEqual(metrics["Inspections"], "1")
        self.assertEqual(metrics["Issues found"], "0")

        delete = await self.client.request("DELETE", "/reports", json={"ids": [report_id]}, headers=headers)
        self.assertEqual(delete.status_code, 200, delete.text)
        self.assertEqual(delete.json()["deleted"], [report_id])
        self.assertEqual(self._count_rows("documents"), 0)
        self.assertEqual(self._count_rows("findings"), 0)
        self.assertEqual(self._count_rows("jobs"), 0)
        self.assertEqual(self._count_rows("predicted_inspections"), 0)

        reports = await self.client.get("/reports", headers=headers)
        self.assertEqual(reports.json()["items"], [])
        dashboard_after_delete = await self.client.get("/dashboard", headers=headers)
        metrics_after_delete = {item["label"]: item["value"] for item in dashboard_after_delete.json()["metrics"]}
        self.assertEqual(metrics_after_delete["Inspections"], "0")
        self.assertEqual(metrics_after_delete["Issues found"], "0")

    async def test_empty_text_upload_returns_422_and_keeps_failed_rows(self) -> None:
        headers = await self._auth_headers()
        upload = await self.client.post(
            "/reports",
            data={
                "project": "Scanned Project",
                "site": "Scanned Site",
                "status": "Reviewing",
                "inspector": "QA",
                "trade": "General",
            },
            files={"file": ("scanned-report.pdf", _empty_pdf_bytes(), "application/pdf")},
            headers=headers,
        )

        self.assertEqual(upload.status_code, 422, upload.text)
        detail = upload.json()["detail"]
        self.assertEqual(detail["diagnostics"]["raw_text_length"], 0)
        self.assertEqual(detail["diagnostics"]["finding_count"], 0)
        self.assertTrue(detail["reportId"].startswith("rpt-"))
        self.assertEqual(self._count_rows("documents"), 1)
        self.assertEqual(self._count_rows("findings"), 0)
        self.assertEqual(self._count_rows("jobs"), 1)

        connection = sqlite3.connect(self.db_path)
        try:
            job = connection.execute("SELECT status, error_message FROM jobs LIMIT 1").fetchone()
            self.assertEqual(job[0], "failed")
            self.assertIn("too short", job[1])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
