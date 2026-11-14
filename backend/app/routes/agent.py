from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repository as repo
from ..agent.graph import thread_snapshot
from ..db import get_db
from ..deps import actor_label, require_auth
from .. import notify
from ..schemas import ApprovalBulkRequest, ApprovalDecisionRequest, ChatRequest
from ..util import parse_date
from ..workflow import chat, resolve_approval

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat")
def agent_chat(payload: ChatRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    if not repo.get_patient(db, payload.patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    thread = thread_snapshot(payload.patient_id)
    if thread["paused"]:
        return {
            "status": "awaiting_approval",
            "reply": (
                "This patient thread is paused for a human-in-the-loop approval. "
                "Resolve the pending approval, then continue."
            ),
            "thread": thread,
        }
    return chat(patient_id=payload.patient_id, message=payload.message, actor=actor_label(user))


@router.get("/threads/{patient_id}")
def get_thread(patient_id: str) -> dict:
    db = get_db()
    if not repo.get_patient(db, patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return thread_snapshot(patient_id)


@router.get("/approvals")
def list_approvals(
    patient_id: str | None = None,
    status: str | None = Query(default="pending"),
    user: dict = Depends(require_auth),
) -> dict:
    db = get_db()
    approvals = repo.list_approvals(
        db, patient_id, status=status or None, audience=user.get("role")
    )
    for approval in approvals:
        approval["priority"] = _priority(approval)
    # Triage order for the pending queue: emergencies, then urgent, then the
    # rest; oldest first within each, so nothing waits behind newer work.
    if status == "pending":
        approvals.sort(key=lambda a: (a["priority"], a.get("created_at") or ""))
    return {"approvals": approvals, "count": len(approvals)}


def _priority(approval: dict) -> int:
    level = notify.urgency(approval)
    return 0 if level == "emergency" else 1 if level == "urgent" else 2


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    approval = repo.get_approval(db, approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval": approval}


@router.post("/approvals/{approval_id}/acknowledge")
def acknowledge(approval_id: str, user: dict = Depends(require_auth)) -> dict:
    """Stop re-paging: the clinician has seen the escalation and is on it."""
    db = get_db()
    approval = repo.get_approval(db, approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if repo.approval_doc_audience(approval) != user.get("role"):
        raise HTTPException(status_code=403, detail="Only the queue that owns this approval can acknowledge it.")
    return {"approval": notify.acknowledge(db, approval_id, actor=actor_label(user))}


@router.post("/approvals/bulk-resolve")
def bulk_resolve(payload: ApprovalBulkRequest, user: dict = Depends(require_auth)) -> dict:
    """Resolve several approvals with one decision (each still audited)."""
    db = get_db()
    results = []
    for approval_id in dict.fromkeys(payload.ids):
        approval = repo.get_approval(db, approval_id)
        if not approval:
            results.append({"id": approval_id, "ok": False, "status": "not_found"})
            continue
        if repo.approval_doc_audience(approval) != user.get("role"):
            results.append({"id": approval_id, "ok": False, "status": "forbidden"})
            continue
        result = resolve_approval(approval_id, approve=payload.approve, actor=actor_label(user), note=payload.note)
        results.append({"id": approval_id, "ok": bool(result.get("ok")), "status": result.get("status")})
    return {"results": results, "resolved": sum(1 for r in results if r["ok"])}


@router.post("/approvals/{approval_id}/resolve")
def resolve(approval_id: str, payload: ApprovalDecisionRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    approval = repo.get_approval(db, approval_id)
    if approval and repo.approval_doc_audience(approval) != user.get("role"):
        raise HTTPException(
            status_code=403,
            detail=(
                "This approval is in the "
                f"{repo.approval_doc_audience(approval)} queue; it cannot be "
                "resolved by a "
                f"{user.get('role', 'unknown')}."
            ),
        )
    if payload.chosen_date is not None:
        if not approval or approval.get("kind") != "resolve_conflict":
            raise HTTPException(status_code=422, detail="chosen_date applies only to conflict resolutions.")
        if not parse_date(payload.chosen_date):
            raise HTTPException(status_code=422, detail="chosen_date must be YYYY-MM-DD")
        if approval.get("mode") == "interrupt":
            raise HTTPException(status_code=422, detail="Resolve this paused run with approve or reject.")
        if not payload.approve:
            raise HTTPException(status_code=422, detail="Entering a confirmed date approves the resolution.")
    result = resolve_approval(
        approval_id,
        approve=payload.approve,
        actor=actor_label(user),
        note=payload.note,
        chosen_date=payload.chosen_date,
    )
    if result.get("ok") and approval and approval.get("kind") == "escalate" and not approval.get("acknowledged_at"):
        notify.acknowledge(get_db(), approval_id, actor=actor_label(user))
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("status") == "not_found" else 409,
            detail=result.get("status"),
        )
    return result


@router.get("/audit")
def audit_log(patient_id: str | None = None, limit: int = 100) -> dict:
    db = get_db()
    return {"audit": repo.list_audit(db, patient_id, limit=limit)}


@router.get("/memories/{patient_id}")
def memories(patient_id: str, q: str | None = None, limit: int = 50) -> dict:
    db = get_db()
    if q:
        from ..agent.memory import semantic_recall

        return {"memories": semantic_recall(db, patient_id, q, k=limit), "query": q}
    return {"memories": repo.list_memories(db, patient_id, limit=limit)}
