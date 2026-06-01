from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile

from ...schemas.reports import BulkDeleteReportsRequest
from ...services.report_service import ReportUploadService
from ..dependencies import AuthContext, get_auth_context, get_report_service, require_tenant_data_access

router = APIRouter(prefix="/reports", dependencies=[Depends(require_tenant_data_access)])


@router.get("")
def list_reports(
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> dict:
    return service.list_reports(tenant_id=context.tenant_id)


@router.delete("")
def bulk_delete_reports(
    payload: BulkDeleteReportsRequest,
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> dict:
    return service.bulk_delete_reports(tenant_id=context.tenant_id, report_ids=payload.ids)


@router.post("", status_code=201)
async def create_report(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project: str = Form(...),
    site: str = Form(...),
    status: str = Form("Reviewing"),
    inspector: str = Form(""),
    trade: str = Form("General"),
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
):
    _ = status
    _ = inspector
    return await service.upload_report(
        background_tasks=background_tasks,
        file=file,
        tenant_id=context.tenant_id,
        project=project,
        site=site,
        trade=trade,
    )


@router.post("/bulk-delete")
def bulk_delete_reports_alias(
    payload: BulkDeleteReportsRequest,
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> dict:
    return service.bulk_delete_reports(tenant_id=context.tenant_id, report_ids=payload.ids)


@router.get("/{report_id}")
def get_report(
    report_id: str,
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> dict:
    return service.get_report(tenant_id=context.tenant_id, report_id=report_id)


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> Response:
    content, filename = service.download_report(tenant_id=context.tenant_id, report_id=report_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.delete("/{report_id}")
def delete_report(
    report_id: str,
    context: AuthContext = Depends(get_auth_context),
    service: ReportUploadService = Depends(get_report_service),
) -> dict:
    return service.delete_report(tenant_id=context.tenant_id, report_id=report_id)
