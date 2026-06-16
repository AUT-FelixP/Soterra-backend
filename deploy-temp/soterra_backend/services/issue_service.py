from __future__ import annotations

from fastapi import HTTPException

from ..analytics import build_issue_detail, build_issues_list, build_tracker_page
from ..repositories.base import RepositoryBackend
from .work_package_service import build_todays_fix_list, build_work_packages


class IssueService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository

    def list_issues(self, *, tenant_id: str) -> dict:
        return build_issues_list(self.repository.load_snapshot(tenant_id))

    def get_issue(self, *, tenant_id: str, issue_id: str) -> dict:
        payload = build_issue_detail(self.repository.load_snapshot(tenant_id), issue_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Issue not found")
        return payload

    def work_packages(self, *, tenant_id: str) -> dict:
        snapshot = self.repository.load_snapshot(tenant_id)
        return {"items": build_work_packages(snapshot.findings)}

    def todays_fix_list(self, *, tenant_id: str) -> dict:
        return build_todays_fix_list(self.repository.load_snapshot(tenant_id).findings)

    def update_issue(
        self,
        *,
        tenant_id: str,
        issue_id: str,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict:
        updated = self.repository.update_issue(
            tenant_id,
            issue_id,
            status=status,
            reinspections=reinspections,
            last_sent_to=last_sent_to,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return build_issue_detail(self.repository.load_snapshot(tenant_id), issue_id) or {"item": updated}

    def tracker(self, *, tenant_id: str, filters: dict) -> dict:
        return build_tracker_page(self.repository.load_snapshot(tenant_id), filters)

    def get_tracker_issue(self, *, tenant_id: str, issue_id: str) -> dict:
        payload = self.get_issue(tenant_id=tenant_id, issue_id=issue_id)
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

    def update_tracker_issue(
        self,
        *,
        tenant_id: str,
        issue_id: str,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict:
        updated = self.repository.update_issue(
            tenant_id,
            issue_id,
            status=status,
            reinspections=reinspections,
            last_sent_to=last_sent_to,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return {"item": updated}
