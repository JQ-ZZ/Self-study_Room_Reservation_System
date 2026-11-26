from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import CurrentUser, get_current_user
from ..deps import get_m1
from ..m1_client import M1Client, M1Error
from ..schemas import RoomOut

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomOut])
def list_rooms(
    _user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> list[RoomOut]:
    """教室主数据由 M1 提供，M2 通过 HTTP 读取，不直接查 M1 数据库。"""
    try:
        items = m1.list_rooms()
    except M1Error as exc:
        raise HTTPException(status_code=503, detail=f"教室主数据暂时不可用：{exc}") from exc
    return [RoomOut(**room) for room in items]
