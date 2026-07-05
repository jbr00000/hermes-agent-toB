"""Per-user memory routes: GET/POST/DELETE /memory.

All scoped to the authenticated user (the same isolation boundary as sessions).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server import memory
from server.deps import get_current_user

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryIn(BaseModel):
    content: str


class CandidateApproveIn(BaseModel):
    content: str | None = None


@router.get("")
def list_mine(user: dict = Depends(get_current_user)):
    return {"memories": memory.list_memories(user["id"])}


@router.post("")
def save_mine(req: MemoryIn, user: dict = Depends(get_current_user)):
    return {"memory": memory.save_memory(user["id"], req.content)}


@router.get("/candidates")
def list_candidates(status: str | None = "pending", user: dict = Depends(get_current_user)):
    return {"candidates": memory.list_memory_candidates(user["id"], status=status)}


@router.post("/candidates/{candidate_id}/approve")
def approve_candidate(
    candidate_id: str,
    req: CandidateApproveIn,
    user: dict = Depends(get_current_user),
):
    approved = memory.approve_memory_candidate(
        user["id"],
        candidate_id,
        content=req.content,
    )
    if approved is None:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    return {"memory": approved}


@router.delete("/candidates/{candidate_id}")
def delete_candidate(candidate_id: str, user: dict = Depends(get_current_user)):
    if not memory.delete_memory_candidate(user["id"], candidate_id):
        raise HTTPException(status_code=404, detail="memory candidate not found")
    return {"deleted": candidate_id}


@router.delete("/{memory_id}")
def delete_mine(memory_id: str, user: dict = Depends(get_current_user)):
    if not memory.delete_memory(user["id"], memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": memory_id}
