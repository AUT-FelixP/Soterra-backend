from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import fitz
import httpx

from soterra_backend.storage import LocalFileStorage


def _pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Tenant isolation test report")
    payload = document.tobytes()
    document.close()
    return payload


class LocalStorageTenantIsolationTest(unittest.TestCase):
    def test_storage_paths_are_tenant_prefixed_and_cannot_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalFileStorage(Path(tmp))
            first = storage.store(
                tenant_id="ten-a",
                document_id="rpt-1",
                filename="../../report.pdf",
                content=b"tenant-a",
                content_type="application/pdf",
            )
            second = storage.store(
                tenant_id="ten-b",
                document_id="rpt-1",
                filename="../../report.pdf",
                content=b"tenant-b",
                content_type="application/pdf",
            )

            self.assertIn(str(Path("ten-a") / "rpt-1"), first.storage_path)
            self.assertIn(str(Path("ten-b") / "rpt-1"), second.storage_path)
            self.assertEqual(storage.read(tenant_id="ten-a", document_id="rpt-1", filename="../../report.pdf"), b"tenant-a")
            self.assertEqual(storage.read(tenant_id="ten-b", document_id="rpt-1", filename="../../report.pdf"), b"tenant-b")
            with self.assertRaises(ValueError):
                storage.store(
                    tenant_id="../ten-b",
                    document_id="rpt-2",
                    filename="report.pdf",
                    content=b"blocked",
                    content_type="application/pdf",
                )


class SupabaseMigrationHardeningTest(unittest.TestCase):
    def test_migration_enables_security_invoker_views_and_tenant_storage_policies(self) -> None:
        migration = (
            Path(__file__).resolve().parents[2]
            / "supabase"
            / "migrations"
            / "20260601000000_production_tenant_hardening.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("security_invoker = true", migration)
        self.assertIn("(storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')", migration)
        self.assertIn("ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS tenant_id", migration)
        self.assertIn("consume_tenant_upload_rate_limit", migration)
        self.assertIn("REVOKE ALL ON", migration)
        self.assertIn("FROM anon", migration)


class ApiTenantIsolationTest(unittest.IsolatedAsyncioTestCase):
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
        self.pdf = _pdf_bytes()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")

    async def _register(self, client: httpx.AsyncClient, suffix: str) -> tuple[dict, dict]:
        response = await client.post(
            "/auth/register",
            json={
                "tenantName": f"Tenant {suffix}",
                "name": f"Admin {suffix}",
                "email": f"admin-{suffix}@example.test",
                "password": "VeryStrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        return payload, {"Authorization": f"Bearer {payload['access_token']}"}

    async def _upload(self, client: httpx.AsyncClient, headers: dict) -> dict:
        response = await client.post(
            "/reports",
            data={"project": "Shared project name", "site": "Shared site", "trade": "General"},
            files={"file": ("report.pdf", self.pdf, "application/pdf")},
            headers=headers,
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["item"]

    async def test_cross_tenant_reports_files_and_forged_header_are_isolated(self) -> None:
        async with await self._client() as client:
            tenant_a, headers_a = await self._register(client, "a")
            tenant_b, headers_b = await self._register(client, "b")
            report_a = await self._upload(client, headers_a)
            report_b = await self._upload(client, headers_b)

            self.assertNotEqual(report_a["id"], report_b["id"])
            self.assertIn(tenant_a["user"]["tenant_id"], report_a["storage_path"])
            self.assertIn(tenant_b["user"]["tenant_id"], report_b["storage_path"])

            forged_headers = {**headers_a, "X-Soterra-Tenant-Id": tenant_b["user"]["tenant_id"]}
            listed = (await client.get("/reports", headers=forged_headers)).json()["items"]
            self.assertTrue(any(item["id"] == report_a["id"] for item in listed))
            self.assertFalse(any(item["id"] == report_b["id"] for item in listed))
            self.assertEqual((await client.get(f"/reports/{report_b['id']}", headers=forged_headers)).status_code, 404)
            self.assertEqual((await client.get(f"/reports/{report_b['id']}/download", headers=forged_headers)).status_code, 404)
            issues_a = (await client.get("/issues", headers=forged_headers)).json()["items"]
            issues_b = (await client.get("/issues", headers=headers_b)).json()["items"]
            self.assertTrue(issues_a)
            self.assertTrue(issues_b)
            self.assertTrue({item["id"] for item in issues_a}.isdisjoint({item["id"] for item in issues_b}))

    async def test_member_directory_is_tenant_admin_only(self) -> None:
        async with await self._client() as client:
            _, admin_headers = await self._register(client, "directory")
            invited = await client.post(
                "/tenants/members",
                json={"name": "Member", "email": "member@example.test", "password": "VeryStrongPassword123!"},
                headers=admin_headers,
            )
            self.assertEqual(invited.status_code, 201, invited.text)
            login = await client.post(
                "/auth/login",
                json={"email": "member@example.test", "password": "VeryStrongPassword123!"},
            )
            self.assertEqual(login.status_code, 200, login.text)
            member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            self.assertEqual((await client.get("/tenants/members", headers=member_headers)).status_code, 403)
            self.assertEqual((await client.get("/reports", headers=member_headers)).status_code, 403)


if __name__ == "__main__":
    unittest.main()
