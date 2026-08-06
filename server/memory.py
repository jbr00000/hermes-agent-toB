"""Per-user persistent memory backed by the shared storage repository."""
from __future__ import annotations

from typing import List

from server.storage import get_repository, init_storage

_db_path_cache: str | None = None  # Backward-compatible test reset hook.


def init_db() -> None:
    init_storage()


def save_memory(user_id: str, content: str) -> dict:
    return get_repository().save_memory(user_id, content)


def list_memories(user_id: str) -> List[dict]:
    return get_repository().list_memories(user_id)


def list_memory_contents(user_id: str) -> List[str]:
    return [memory["content"] for memory in list_memories(user_id)]


def delete_memory(user_id: str, memory_id: str) -> bool:
    return get_repository().delete_memory(user_id, memory_id)


def save_memory_candidate(
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> dict:
    return get_repository().save_memory_candidate(
        user_id, session_id, user_message, assistant_message
    )


def list_memory_candidates(user_id: str, status: str | None = "pending") -> List[dict]:
    return get_repository().list_memory_candidates(user_id, status)


def delete_memory_candidate(user_id: str, candidate_id: str) -> bool:
    return get_repository().delete_memory_candidate(user_id, candidate_id)


def approve_memory_candidate(
    user_id: str,
    candidate_id: str,
    content: str | None = None,
) -> dict | None:
    return get_repository().approve_memory_candidate(user_id, candidate_id, content)
