from soterra_backend.agent.local_agent import _issue_discovery_response
from soterra_backend.extractors.model.prompts import SYSTEM_PROMPT
from soterra_backend.models import RepositorySnapshot
from soterra_backend.services.issue_query_service import IssueQueryService


class SnapshotRepository:
    def __init__(self, findings):
        self.snapshot = RepositorySnapshot(projects=[], documents=[], jobs=[], findings=findings, predicted_inspections=[])

    def load_snapshot(self, tenant_id):
        assert tenant_id == "tenant-a"
        return self.snapshot


def finding(issue_id, project, address, location, trade="Passive Fire", severity="High"):
    return {"id": issue_id, "project_name": project, "project_slug": project.lower(), "address": address, "site_name": project, "location": location, "title": "Fire collar missing", "description": "A fire collar is missing from the service penetration.", "trade": trade, "severity": severity, "status": "Open", "confidence": .9, "required_fix": "Install the approved collar.", "evidence_required": ["Close-out photo"], "source_quote": "Fire collar missing", "source_page": 3, "document_status": "Completed"}


def test_prompt_is_location_first_and_requires_full_intelligence():
    for phrase in ("previous row", "source quote", "descriptive", "predictive", "Exact issue location needs manual confirmation"):
        assert phrase.lower() in SYSTEM_PROMPT.lower()


def test_query_filters_and_drillthrough_are_consistent():
    service = IssueQueryService(SnapshotRepository([finding("one", "Kauri", "1 Main St", "Level 2 Unit 4"), finding("two", "Rimu", "2 Main St", "Roof", trade="Roofing")]))
    result = service.query("tenant-a", {"project": "Kauri", "trade": "Passive Fire", "severity": "High"})
    assert result["total"] == result["metrics"]["total"] == 1
    assert result["items"][0]["source_quote"] == "Fire collar missing"
    assert result["items"][0]["what_to_do_next"] == "Install the approved collar."
    assert service.drillthrough("tenant-a", "one")["exact_location"] == "Level 2 Unit 4"


def test_broad_open_issue_query_requests_location_clarification():
    repository = SnapshotRepository([finding("one", "Kauri", "1 Main St", "Level 2 Unit 4"), finding("two", "Rimu", "2 Main St", "Roof")])
    answer, payload = _issue_discovery_response(repository, "tenant-a", "What are the open issues?", None)
    assert "Which location" in answer
    assert payload["type"] == "location_clarification"
    assert len(payload["options"]) == 2
