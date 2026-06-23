from fastapi import APIRouter, Depends, HTTPException

from ...services.issue_query_service import IssueQueryService
from ..dependencies import AuthContext, get_auth_context, get_repository, require_tenant_data_access

router = APIRouter(prefix="/analytics", dependencies=[Depends(require_tenant_data_access)])

def service(repository=Depends(get_repository)) -> IssueQueryService:
    return IssueQueryService(repository)

@router.get("/facets")
def facets(context: AuthContext = Depends(get_auth_context), query: IssueQueryService = Depends(service)): return query.facets(context.tenant_id)

@router.post("/issues/query")
def issues_query(payload: dict, context: AuthContext = Depends(get_auth_context), query: IssueQueryService = Depends(service)): return query.query(context.tenant_id, payload)

@router.get("/location-tree")
def location_tree(context: AuthContext = Depends(get_auth_context), query: IssueQueryService = Depends(service)): return {"items": query.location_tree(context.tenant_id)}

@router.get("/data-quality")
def data_quality(context: AuthContext = Depends(get_auth_context), query: IssueQueryService = Depends(service)): return query.data_quality(context.tenant_id)

@router.get("/issues/{issue_id}/drillthrough")
def drillthrough(issue_id: str, context: AuthContext = Depends(get_auth_context), query: IssueQueryService = Depends(service)):
    item = query.drillthrough(context.tenant_id, issue_id)
    if not item: raise HTTPException(404, "Issue not found")
    return item
