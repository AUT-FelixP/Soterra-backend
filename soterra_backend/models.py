from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Severity = Literal["Low", "Medium", "High", "Critical"]
IssueStatus = Literal["Open", "Ready", "Closed"]
ReportStatus = Literal["Reviewing", "Completed", "In progress"]
RiskLevel = Literal["Low", "Medium", "High"]
TenantRole = Literal["admin", "member"]


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedFinding(StrictSchemaModel):
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3)
    severity: Severity
    category: str = Field(default="General", max_length=120)
    trade: str = Field(default="General", max_length=120)
    location: str | None = Field(default=None, max_length=160)
    unit_label: str | None = Field(default=None, max_length=80)
    status: IssueStatus = "Open"
    recurrence_risk: int = Field(default=30, ge=0, le=100)


class PredictedInspection(StrictSchemaModel):
    inspection_type: str = Field(min_length=2, max_length=120)
    site_name: str = Field(min_length=2, max_length=160)
    expected_date: str
    risk_level: RiskLevel
    source: str = Field(min_length=2, max_length=240)


class ExtractionResult(StrictSchemaModel):
    project_name: str = Field(min_length=2, max_length=160)
    site_name: str = Field(min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=240)
    inspection_type: str = Field(min_length=2, max_length=120)
    trade: str = Field(min_length=2, max_length=120)
    inspector: str = Field(min_length=2, max_length=120)
    report_date: str
    summary: str = Field(min_length=10, max_length=1200)
    overall_outcome: ReportStatus = "Reviewing"
    units: list[str] = Field(default_factory=list)
    findings: list[ExtractedFinding] = Field(default_factory=list)
    predicted_inspections: list[PredictedInspection] = Field(default_factory=list)


class StoredFile(StrictSchemaModel):
    storage_path: str
    download_url: str | None = None


class TenantUser(StrictSchemaModel):
    id: str
    tenant_id: str
    tenant_name: str
    name: str
    email: str
    role: TenantRole


class AuthSession(StrictSchemaModel):
    user: TenantUser
    access_token: str | None = None
    expires_at: str | None = None


class RepositorySnapshot(StrictSchemaModel):
    projects: list[dict]
    documents: list[dict]
    jobs: list[dict]
    findings: list[dict]
    predicted_inspections: list[dict]


class IngestionOutcome(StrictSchemaModel):
    item: dict
    is_duplicate: bool = False
