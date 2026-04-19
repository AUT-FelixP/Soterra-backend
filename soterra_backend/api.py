from __future__ import annotations

from functools import lru_cache

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .analytics import (
    build_company_page,
    build_dashboard_insights_preview,
    build_dashboard_live_tracker,
    build_dashboard_overview,
    build_dashboard_risk,
    build_dashboard_top_failures,
    build_dashboard_upcoming_risk,
    build_insights_page,
    build_inspection_risk_page,
    build_issue_detail,
    build_issues_list,
    build_legacy_insights_summary,
    build_performance_page,
    build_project_page,
    build_report_detail,
    build_report_list,
    build_tracker_page,
)
from .config import Settings
from .repository import build_repository
from .service import ReportIngestionService, UploadContext
from .storage import build_storage


def create_app() -> FastAPI:
    settings = Settings.from_env()
    repository = build_repository(settings)
    repository.initialize()
    storage = build_storage(settings)
    ingestion_service = ReportIngestionService(
        settings=settings,
        repository=repository,
        storage=storage,
    )

    app = FastAPI(title="Soterra Backend", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "soterra-backend",
            "repositoryMode": settings.repository_mode,
            "storageMode": settings.storage_mode,
            "extractorMode": settings.extractor_mode,
            "packageExtractor": settings.package_extractor,
            "modelExtractor": settings.model_extractor,
            "modelExtractionEnabled": settings.allow_model_extraction,
            "processInline": settings.process_inline,
        }

    @app.get("/reports")
    def list_reports() -> dict:
        return build_report_list(repository.load_snapshot())

    @app.get("/reports/{report_id}")
    def get_report(report_id: str) -> dict:
        payload = build_report_detail(repository.load_snapshot(), report_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Report not found")
        return payload

    @app.post("/reports", status_code=201)
    async def create_report(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        project: str = Form(...),
        site: str = Form(...),
        status: str = Form("Reviewing"),
        inspector: str = Form(""),
        trade: str = Form("General"),
    ) -> dict:
        _ = status
        _ = inspector
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        upload_ctx = UploadContext(
            filename=file.filename or "uploaded-report.pdf",
            content=content,
            content_type=file.content_type or "application/pdf",
            project_name=project,
            site_name=site,
            trade=trade or "General",
            address=None,
        )

        outcome, start = ingestion_service.start_ingest(upload_ctx)
        if outcome:
            payload = {"item": outcome.item, "isDuplicate": outcome.is_duplicate}
            return JSONResponse(payload, status_code=200)

        assert start is not None
        if settings.process_inline:
            report = ingestion_service.finish_ingest(start, upload_ctx)
            return JSONResponse({"item": report, "isDuplicate": False}, status_code=201)

        # Async mode: return immediately and run extraction after the response.
        background_tasks.add_task(ingestion_service.finish_ingest, start, upload_ctx)
        placeholder = repository.get_report(start.document_id) or {"id": start.document_id}
        return JSONResponse(
            {"item": placeholder, "isDuplicate": False, "isProcessing": True},
            status_code=202,
        )

    @app.get("/issues")
    def list_issues() -> dict:
        return build_issues_list(repository.load_snapshot())

    @app.get("/issues/{issue_id}")
    def get_issue(issue_id: str) -> dict:
        payload = build_issue_detail(repository.load_snapshot(), issue_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Issue not found")
        return payload

    @app.patch("/issues/{issue_id}")
    def patch_issue(issue_id: str, payload: dict = Body(default_factory=dict)) -> dict:
        updated = repository.update_issue(
            issue_id,
            status=payload.get("status"),
            reinspections=payload.get("reinspections"),
            last_sent_to=payload.get("lastSentTo"),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return build_issue_detail(repository.load_snapshot(), issue_id) or {"item": updated}

    @app.get("/dashboard")
    def dashboard() -> dict:
        return build_dashboard_overview(repository.load_snapshot())

    @app.get("/dashboard/company")
    def dashboard_company() -> dict:
        return build_company_page(repository.load_snapshot())

    @app.get("/dashboard/performance")
    def dashboard_performance(inspectionType: str = "All types") -> dict:
        return build_performance_page(repository.load_snapshot(), inspectionType)

    @app.get("/dashboard/insights")
    def dashboard_insights(inspectionType: str = "All inspection types") -> dict:
        return build_insights_page(repository.load_snapshot(), inspectionType)

    @app.get("/insights")
    def insights_summary() -> dict:
        return build_legacy_insights_summary(repository.load_snapshot())

    @app.get("/dashboard/project/{slug}")
    def dashboard_project(slug: str) -> dict:
        payload = build_project_page(repository.load_snapshot(), slug)
        if not payload:
            raise HTTPException(status_code=404, detail="Project not found")
        return payload

    @app.get("/dashboard/risk")
    def dashboard_risk(site: str = "All sites", window: str = "30d", inspectionId: str | None = None) -> dict:
        return build_dashboard_risk(repository.load_snapshot(), site, window, inspectionId)

    @app.get("/dashboard/live-tracker")
    def dashboard_live_tracker() -> dict:
        return build_dashboard_live_tracker(repository.load_snapshot())

    @app.get("/dashboard/top-failures")
    def dashboard_top_failures(inspectionType: str | None = None) -> dict:
        return build_dashboard_top_failures(repository.load_snapshot(), inspectionType)

    @app.get("/dashboard/upcoming-risk")
    def dashboard_upcoming_risk() -> dict:
        return build_dashboard_upcoming_risk(repository.load_snapshot())

    @app.get("/dashboard/insights-preview")
    def dashboard_insights_preview() -> dict:
        return build_dashboard_insights_preview(repository.load_snapshot())

    @app.get("/inspection-risk")
    def inspection_risk(site: str | None = None, dateRange: str | None = None, inspectionType: str | None = None) -> dict:
        return build_inspection_risk_page(repository.load_snapshot(), site, dateRange, inspectionType)

    @app.get("/tracker")
    def tracker(
        site: str | None = None,
        search: str | None = None,
        status: str | None = None,
        type: str | None = None,
        dateRange: str | None = None,
        issueId: str | None = None,
    ) -> dict:
        return build_tracker_page(
            repository.load_snapshot(),
            {
                "site": site,
                "search": search,
                "status": status,
                "type": type,
                "dateRange": dateRange,
                "issueId": issueId,
            },
        )

    @app.get("/tracker/{issue_id}")
    def tracker_issue(issue_id: str) -> dict:
        payload = build_issue_detail(repository.load_snapshot(), issue_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Issue not found")
        item = payload["item"]
        return {
            "item": {
                "id": item["id"],
                "issue": item["description"],
                "site": item["site"],
                "dateIdentified": item["dateIdentified"],
                "status": item["status"],
                "reinspections": item["reinspections"],
            }
        }

    @app.patch("/tracker/{issue_id}")
    def patch_tracker_issue(issue_id: str, payload: dict = Body(default_factory=dict)) -> dict:
        updated = repository.update_issue(
            issue_id,
            status=payload.get("status"),
            reinspections=payload.get("reinspections"),
            last_sent_to=payload.get("lastSentTo"),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return {"item": updated}

    return app


@lru_cache(maxsize=1)
def _cached_app() -> FastAPI:
    return create_app()


app = _cached_app()
