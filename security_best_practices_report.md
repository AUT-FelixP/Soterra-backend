# Soterra Backend Security Audit

Audit date: 2026-04-22

## Executive Summary

The backend now has a tenant-aware data model and most report, issue, dashboard, and member repository calls are scoped by `tenant_id`. That is a good base for SaaS isolation. However, the current in-app auth is not production-safe because protected routes trust caller-supplied user and tenant headers instead of a cryptographically verifiable session or JWT. Before real clients use the system, fix the critical auth issue, upload limits/type validation, local storage filename traversal, demo credentials, and production deployment hardening.

Positive controls observed:

- Tenant-scoped read/write queries exist for reports, issues, dashboard snapshots, members, and deletes.
- Emails are normalized in application code and case-insensitive uniqueness indexes were added for SQLite and Supabase.
- SQL access mostly uses parameterized SQLite queries or Supabase query builder calls.
- `.env` and `.env.*` are ignored by git; current `.env` is not tracked.

## Critical Findings

### SEC-001: Header-based tenant sessions are not real authentication

Status: Fixed in code. The backend now issues opaque bearer tokens on register/login, stores only token hashes in `auth_sessions`, validates `Authorization: Bearer <token>` in middleware, and derives tenant/user headers from the verified session instead of trusting caller-provided headers.

Severity: Critical

Location:

- `soterra_backend/api.py:61-67`
- `soterra_backend/api.py:100-108`
- `soterra_backend/models.py:59-65`

Evidence:

```python
tenant_id = request.headers.get("X-Soterra-Tenant-Id")
user_id = request.headers.get("X-Soterra-User-Id")
...
if not repository.get_user_session(user_id=user_id, tenant_id=tenant_id):
    return JSONResponse({"detail": "Invalid tenant session."}, status_code=401)
```

The login endpoint returns only `AuthSession(user=...)`; there is no signed token, opaque session secret, expiry, refresh flow, or revocation check.

Impact:

Anyone who obtains or guesses a valid `user_id` and `tenant_id` pair can impersonate that user by setting headers. User IDs are random-looking but are not authentication secrets and should not be treated as bearer credentials.

Fix:

Replace `X-Soterra-User-Id` / `X-Soterra-Tenant-Id` as the trust boundary with either Supabase Auth JWT verification or a server-issued opaque session token. Store only a hashed session token in the database, require `Authorization: Bearer <token>`, include expiry and revocation, and derive tenant/user from the verified token on the backend.

Mitigation:

Until this is fixed, do not expose the backend directly to clients. Put it behind a trusted frontend proxy and assume this is temporary only.

False positive notes:

If an upstream gateway already signs and verifies these headers, document that control and ensure the backend rejects direct public access. No such gateway verification is visible in this repo.

## High Findings

### SEC-002: Upload endpoint reads the full file into memory and does not validate PDF type or size

Status: Fixed in code. The upload endpoint now reads in bounded chunks using `SOTERRA_MAX_UPLOAD_BYTES`, rejects oversized uploads with 413, and requires PDF MIME/magic-byte validation before storage or extraction.

Severity: High

Location:

- `soterra_backend/api.py:220-240`
- `soterra_backend/service.py:145-165`
- `soterra_backend/text_extraction.py:9-27`

Evidence:

```python
content = await file.read()
...
content_type=file.content_type or "application/pdf"
```

Impact:

An authenticated user can upload very large files or non-PDF content, causing memory pressure, long PDF/OCR processing, OpenAI cost spikes, or parser exposure against PyMuPDF/OCR libraries.

Fix:

Add an app-level max upload size, reject empty/oversized bodies, validate `%PDF` magic bytes plus an allowlisted content type, and keep Vercel/edge request body limits aligned. Consider page-count limits before OCR/model extraction.

Mitigation:

Add rate limits for `/reports`, tenant-level quotas, and monitoring for upload failures and extraction latency.

### SEC-003: Local file storage uses the original upload filename without sanitizing it

Status: Fixed in code. Local storage now sanitizes filenames with the shared safe-name helper, resolves paths, verifies writes stay under the document storage directory, and refuses deletes outside the configured storage root.

Severity: High

Location:

- `soterra_backend/storage.py:20-27`

Evidence:

```python
destination = self.root_dir / document_id / filename
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(content)
```

Impact:

If local storage is used, a filename containing path traversal segments may write outside the intended storage directory. This is especially risky in local deployments, test environments, or any accidental production run with `SOTERRA_STORAGE_MODE=local`.

Fix:

Use the same `_safe_storage_filename()` sanitization for local storage as Supabase storage, resolve the path, and verify the resolved destination remains inside the intended document directory.

Mitigation:

Prefer Supabase storage in production and keep the local storage directory outside source-controlled paths.

### SEC-004: Default demo admin account is created with a known password

Status: Fixed in code. Demo account creation is now disabled by default and requires both `SOTERRA_BOOTSTRAP_DEMO_ACCOUNT=true` and an explicit `SOTERRA_DEMO_ADMIN_PASSWORD` value of at least 12 characters.

Severity: High

Location:

- `soterra_backend/repository.py:687-697`

Evidence:

```python
(_DEFAULT_ADMIN_ID, _DEFAULT_TENANT_ID, "Demo Admin", "admin@soterra.local", _hash_password("password"), "admin", timestamp)
```

Impact:

If production accidentally runs in SQLite/local mode, the default admin account can be used by anyone who knows the demo credentials.

Fix:

Create demo credentials only when an explicit local/demo environment flag is set. In production, fail startup unless auth/bootstrap is configured securely.

Mitigation:

Delete the default account from any real database and avoid SQLite mode for hosted production.

### SEC-005: No visible login/register rate limiting or account lockout

Status: Fixed in code. Register, login, and invite routes now use in-process IP/subject throttling and return 429 after repeated attempts. Edge/CDN throttling is still recommended for production defense in depth.

Severity: High

Location:

- `soterra_backend/api.py:84-108`
- `soterra_backend/api.py:124-136`

Evidence:

The auth endpoints accept credentials and invitations directly; no rate limit, lockout, throttling, or CAPTCHA/backoff control is visible in app code.

Impact:

Attackers can brute-force passwords, enumerate registration emails via conflict responses, or spam tenant/member creation.

Fix:

Add rate limiting by IP and account/email, add short lockouts or exponential backoff after failed login attempts, and monitor auth failure rates.

Mitigation:

Apply Vercel/WAF/CDN rate limits immediately if available while app-level limits are implemented.

## Medium Findings

### SEC-006: Supabase migrations do not enable Row Level Security policies

Status: Fixed in code. Supabase migrations now enable RLS on tenant-owned tables and add tenant-isolation policies for authenticated users. The backend still uses the service-role key server-side, so service-role rotation and strict environment separation remain required.

Severity: Medium

Location:

- `supabase/migrations/20260416000000_soterra_backend.sql`
- `supabase/migrations/20260422000000_multi_tenant_upgrade.sql`

Evidence:

No `enable row level security` or tenant policies are defined for `users`, `tenants`, `projects`, `documents`, `findings`, or `predicted_inspections`.

Impact:

The backend currently uses the Supabase service-role key, which bypasses RLS. If that key leaks or a backend code path misses a tenant filter, the database will not provide defense-in-depth isolation.

Fix:

For production, move client-facing auth to Supabase Auth/JWTs or server-issued sessions. Add RLS policies for tenant-owned rows and avoid exposing service-role access outside trusted backend-only code.

Mitigation:

Keep `SUPABASE_SERVICE_ROLE_KEY` only in backend environment variables, rotate it before client handoff, and never include it in frontend deployments or shared archives.

### SEC-007: Interactive docs are public by default and explicitly exempted from auth

Status: Fixed in code. FastAPI docs, ReDoc, and OpenAPI are disabled by default in production and only enabled when `SOTERRA_ENABLE_DOCS=true` or in non-production environments.

Severity: Medium

Location:

- `soterra_backend/api.py:47`
- `soterra_backend/api.py:55-57`

Evidence:

```python
app = FastAPI(title="Soterra Backend", version="0.1.0")
...
"/docs",
"/redoc",
"/openapi.json",
```

Impact:

Public docs expose all endpoints, request shapes, and operational details. This makes brute force, endpoint probing, and tenant/header abuse easier.

Fix:

Disable docs in production with `docs_url=None`, `redoc_url=None`, and `openapi_url=None`, or protect docs behind admin-only auth/network allowlists.

Mitigation:

Allow docs only for local/staging environments.

### SEC-008: Password hashing is custom PBKDF2 instead of an established password library

Status: Fixed in code. New passwords are hashed with `argon2-cffi`; existing PBKDF2 hashes remain verifiable for migration compatibility.

Severity: Medium

Location:

- `soterra_backend/repository.py:19`
- `soterra_backend/repository.py:1223-1235`

Evidence:

```python
_PASSWORD_ITERATIONS = 120_000
digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PASSWORD_ITERATIONS).hex()
```

Impact:

PBKDF2 is better than a fast hash, but the implementation is custom and the iteration count may be weak for current password-cracking hardware. There is also no visible rehash-on-login or password policy.

Fix:

Use a maintained password hashing library such as `argon2-cffi` or `passlib` with Argon2id/bcrypt, enforce minimum password length, and add rehash-on-login migration support.

Mitigation:

Increase PBKDF2 iterations temporarily if changing libraries is delayed.

### SEC-009: Health endpoint exposes internal mode and extractor details

Status: Fixed in code. Public `/health` now returns only `{ "status": "ok" }`.

Severity: Medium

Location:

- `soterra_backend/api.py:70-81`

Evidence:

```python
"repositoryMode": settings.repository_mode,
"storageMode": settings.storage_mode,
"extractorMode": settings.extractor_mode,
"packageExtractor": settings.package_extractor,
"modelExtractor": settings.model_extractor,
```

Impact:

An unauthenticated caller can learn storage/repository/extractor modes and model/extraction configuration, which helps attackers target dependencies and expensive processing paths.

Fix:

Return only `{ "status": "ok" }` publicly. Move detailed diagnostics to an authenticated admin-only endpoint.

Mitigation:

Restrict `/health` details by environment.

## Low Findings

### SEC-010: Request bodies use plain dicts instead of explicit Pydantic input models

Status: Fixed in code. Auth, invite, bulk-delete, and issue update request bodies now use explicit Pydantic models with `extra="forbid"` and field constraints.

Severity: Low

Location:

- `soterra_backend/api.py:86`
- `soterra_backend/api.py:100`
- `soterra_backend/api.py:126`
- `soterra_backend/api.py:164`
- `soterra_backend/api.py:279`
- `soterra_backend/api.py:386`

Evidence:

```python
payload: dict = Body(default_factory=dict)
```

Impact:

Current handlers manually pick allowed keys, so immediate mass assignment risk is limited. However, dict-based request handling makes validation inconsistent and increases the chance of future over-posting bugs.

Fix:

Add Pydantic request models with `extra="forbid"` for auth, member invite, bulk delete, and issue update routes.

## Notes On Areas Reviewed

- SQL injection: no obvious direct SQL injection found in request-facing queries; SQLite calls are parameterized, and fixed internal table names are used for migration/backfill.
- Tenant isolation: report, issue, dashboard, member, and delete queries are tenant-scoped in the repository. This is good, but SEC-001 still means the caller identity is not cryptographically proven.
- Secrets: a local `.env` contains backend secrets and is ignored by git. It is not tracked, but rotate service keys before client handoff and do not share local `.env` files.
- Dependencies: locked FastAPI, Starlette, python-multipart, and Uvicorn versions appear recent in `uv.lock`. Continue running dependency audits before deployment.

## Recommended Fix Order

1. Replace header-based auth with a signed JWT or opaque server-side session token.
2. Add upload size/type validation and path-safe local storage.
3. Remove or gate demo credentials.
4. Add auth rate limiting and lockouts.
5. Disable/protect docs and reduce public health output.
6. Add Supabase RLS policies as defense-in-depth.
7. Move password hashing to Argon2id/bcrypt and add Pydantic request models.

## Secondary Audit After Fixes

Secondary audit date: 2026-04-22

Result: All originally identified findings are fixed in code.

Verification performed:

- Compiled all backend Python modules successfully with `python -m compileall -f soterra_backend`.
- Ran backend route tests successfully with `python -m unittest discover -s soterra_backend\tests -p "test_*.py"`.
- Ran a security smoke test covering registration, bearer-token access, forged tenant/user header resistance, duplicate email rejection, non-PDF upload rejection, path-safe local storage, and hashed token storage.
- Re-scanned backend files for the original risky patterns: unbounded `file.read()`, dict request bodies, unsafe local storage destination construction, dev/debug server flags, wildcard CORS, shell execution, file-serving sinks, missing RLS, hardcoded demo password use, and public docs/health details.

Secondary audit notes:

- `pbkdf2_sha256` remains only as a legacy verifier so existing users can still log in after the Argon2 migration. New password hashes use Argon2.
- `admin@soterra.local` remains only as an optional demo account email behind `SOTERRA_BOOTSTRAP_DEMO_ACCOUNT=true`; there is no hardcoded demo password.
- RLS policy lines appear in the secondary scan because the migrations now intentionally enable RLS and create tenant-isolation policies.
- Production still needs operational controls outside this repo: keep `SUPABASE_SERVICE_ROLE_KEY` backend-only, rotate secrets before handoff, configure Vercel/Supabase environment variables carefully, and apply edge/CDN rate limits as defense in depth.
