from __future__ import annotations

from fastapi import APIRouter


router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


@router.get("")
def list_entries() -> dict[str, list[dict[str, str]]]:
    return {"entries": []}


@router.get("/{entry_id}")
def get_entry(entry_id: str) -> dict[str, str]:
    return {"entry_id": entry_id}
