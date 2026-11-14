"""TCM worklist: who needs a post-discharge contact or visit next."""

from fastapi import APIRouter

from .. import tcm
from ..db import demo_today, get_db

router = APIRouter(prefix="/tcm", tags=["tcm"])


@router.get("/worklist")
def worklist() -> dict:
    return tcm.worklist(get_db(), demo_today())
