"""Staff, assignment and the signed-in person's worklist.

``owner`` on a follow-up is the outside party who has to do it (the cardiology
clinic). ``assignee`` is the person on this care team responsible for chasing
it. My work lists what is assigned to me, plus the unassigned work my role
picks up (clinicians: medication reconciliation and escalations).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import repository as repo
from .. import tcm
from ..db import demo_today, get_db
from ..deps import actor_label, require_auth
from ..schemas import MED_REC_CATEGORY

router = APIRouter(tags=["work"])


class AssignRequest(BaseModel):
    user_id: str | None = None  # null unassigns


@router.get("/users")
def list_users() -> dict:
    users = [
        {"id": u["id"], "name": u["name"], "role": u.get("role", "coordinator")}
        for u in get_db().users.find({}, {"_id": 0, "id": 1, "name": 1, "role": 1})
    ]
    users.sort(key=lambda u: (u["role"], u["name"]))
    return {"users": users}


@router.post("/obligations/{obligation_id}/assign")
def assign(obligation_id: str, payload: AssignRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    obligation = repo.get_obligation(db, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    assignee = None
    if payload.user_id:
        target = db.users.find_one({"id": payload.user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if obligation.get("category") == MED_REC_CATEGORY and target.get("role") != "clinician":
            raise HTTPException(status_code=422, detail="Medication reconciliation is assigned to a clinician.")
        assignee = {"id": target["id"], "name": target["name"], "role": target.get("role", "coordinator")}
    before = obligation.get("assignee")
    db.obligations.update_one({"id": obligation_id}, {"$set": {"assignee": assignee}})
    repo.audit(
        db,
        patient_id=obligation["patient_id"],
        actor=actor_label(user),
        action="Assigned follow-up" if assignee else "Unassigned follow-up",
        detail=f"{obligation['description']} → {assignee['name'] if assignee else 'nobody'}",
        before={"assignee": before},
        after={"assignee": assignee},
    )
    return {"obligation": repo.present_obligation(repo.get_obligation(db, obligation_id), demo_today())}


@router.get("/work/me")
def my_work(user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    today = demo_today()
    role = user.get("role", "coordinator")
    soon = (today + timedelta(days=7)).isoformat()
    names = {p["id"]: p["name"] for p in db.patients.find({}, {"_id": 0, "id": 1, "name": 1})}

    open_items = [o for o in repo.list_obligations(db, None, include_closed=False, today=today)]
    mine = [o for o in open_items if (o.get("assignee") or {}).get("id") == user["id"]]

    def for_my_role(o: dict) -> bool:
        is_clinical = o.get("category") == MED_REC_CATEGORY
        return is_clinical if role == "clinician" else not is_clinical

    unassigned = [
        o for o in open_items
        if not o.get("assignee") and for_my_role(o) and (o.get("is_overdue") or (o.get("due_date") or "9999") <= soon)
    ]
    for item in mine + unassigned:
        item["patient_name"] = names.get(item["patient_id"])
    key = lambda o: (not o.get("is_overdue"), o.get("due_date") or "9999")  # noqa: E731
    approvals = repo.list_approvals(db, status="pending", audience=role)
    lanes = tcm.worklist(db, today)["lanes"]
    return {
        "today": today.isoformat(),
        "role": role,
        "assigned": sorted(mine, key=key),
        "unassigned": sorted(unassigned, key=key)[:50],
        "approvals_pending": len(approvals),
        "tcm": lanes if role == "coordinator" else {"contact_due": [], "contact_missed": [], "visit_unbooked": [], "visit_missed": []},
    }
