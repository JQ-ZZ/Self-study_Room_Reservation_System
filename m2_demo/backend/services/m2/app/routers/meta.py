from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..deps import get_db, get_m1

router = APIRouter(tags=["meta"])

GIT_SHA = os.getenv("GIT_SHA", "dev")
GIT_TAG = os.getenv("GIT_TAG", "dev")


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "service": "m2", "database": db_ok}


@router.get("/version")
def version() -> dict:
    return {
        "service": "m2",
        "git_sha": GIT_SHA,
        "git_tag": GIT_TAG,
        "build_time": os.getenv("BUILD_TIME", "local"),
    }


@router.get("/meta/runtime")
def runtime(m1=Depends(get_m1)) -> dict:
    """演示页顶栏：显示 M2 到 M1 的连通性。"""
    from ..m1_client import M1Error

    try:
        m1_state = m1.state()
        m1_ok = True
        failure_mode = m1_state.get("failure_mode", False)
    except M1Error as exc:
        m1_ok = False
        failure_mode = None
        m1_state = {"error": str(exc)}
    return {"m1_reachable": m1_ok, "m1_failure_mode": failure_mode, "m1": m1_state}
