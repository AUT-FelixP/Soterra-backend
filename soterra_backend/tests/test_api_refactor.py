from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.responses import JSONResponse

from soterra_backend.api.dependencies import get_email_service, get_report_service, get_repository, get_storage
from soterra_backend.models import AuthSession, TenantUser


class FakeEmailService:
    def send_registration_email(self, **kwargs) -> bool:
        return False

    def send_password_reset_email(self, **kwargs) -> bool:
        return False


class FakeRepository:
    def __init__(self, *, tenant_id: str = "ten-token", user_id: str = "usr-token") -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.session = AuthSession(
            user=TenantUser(
                id=user_id,
                tenant_id=tenant_id,
                tenant_name="Token Tenant",
                name="Token User",
                email="token@example.test",
                role="admin",
            ),
            access_token="valid-token",
            expires_at="2030-01-01T00:00:00+00:00",
        )
        self.session_calls: list[tuple[str, str]] = []

    def get_auth_session(self, *, access_token: str) -> AuthSession | None:
        return self.session if access_token == "valid-token" else None

    def get_user_session(self, *, user_id: str, tenant_id: str) -> AuthSession | None:
        self.session_calls.append((tenant_id, user_id))
        if user_id == self.user_id and tenant_id == self.tenant_id:
            return self.session
        return None

    def register_tenant(self, *, tenant_name: str, name: str, email: str, password: str) -> AuthSession:
        return self.session.model_copy(
            update={
                "user": self.session.user.model_copy(
                    update={"tenant_name": tenant_name, "name": name, "email": email.strip().lower()}
                )
            }
        )


class FakeStorage:
    pass


class FakeReportService:
    def __init__(self) -> None:
        self.upload_calls: list[dict] = []

    async def upload_report(self, **kwargs):
        self.upload_calls.append(kwargs)
        return JSONResponse({"item": {"id": "rpt-service"}, "isDuplicate": False}, status_code=201)


class ApiRefactorTest(unittest.IsolatedAsyncioTestCase):
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
        self.fake_repo = FakeRepository()
        self.app.state.repository = self.fake_repo

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            headers={"accept": "application/json"},
        )

    async def test_app_startup_registers_feature_routers(self) -> None:
        paths = {route.path for route in self.app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/reports", paths)
        self.assertIn("/issues/{issue_id}", paths)
        self.assertIn("/tracker/{issue_id}", paths)
        self.assertIn("/agent/chat", paths)

    async def test_auth_public_routes_remain_public(self) -> None:
        self.app.dependency_overrides[get_repository] = lambda: self.fake_repo
        self.app.dependency_overrides[get_email_service] = lambda: FakeEmailService()
        async with await self._client() as client:
            self.assertEqual((await client.get("/health")).status_code, 200)
            response = await client.post(
                "/auth/register",
                json={
                    "tenantName": "Public Tenant",
                    "name": "Public Admin",
                    "email": "public@example.test",
                    "password": "VeryStrongPassword123!",
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("Authentication required", response.text)

    async def test_protected_routes_require_auth(self) -> None:
        async with await self._client() as client:
            response = await client.get("/reports")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication required."})

    async def test_report_upload_calls_service_with_middleware_tenant_context(self) -> None:
        fake_service = FakeReportService()
        self.app.dependency_overrides[get_report_service] = lambda: fake_service
        async with await self._client() as client:
            response = await client.post(
                "/reports",
                data={"project": "Kauri", "site": "Kauri", "trade": "General"},
                files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
                headers={
                    "Authorization": "Bearer valid-token",
                    "X-Soterra-Tenant-Id": "ten-client",
                    "X-Soterra-User-Id": "usr-client",
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(fake_service.upload_calls[0]["tenant_id"], "ten-token")

    async def test_dependency_overrides_with_fake_repository_and_storage(self) -> None:
        self.app.dependency_overrides[get_repository] = lambda: self.fake_repo
        self.app.dependency_overrides[get_storage] = lambda: FakeStorage()
        async with await self._client() as client:
            response = await client.get("/auth/session", headers={"Authorization": "Bearer valid-token"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["tenant_id"], "ten-token")
        self.assertEqual(self.fake_repo.session_calls[-1], ("ten-token", "usr-token"))


if __name__ == "__main__":
    unittest.main()
