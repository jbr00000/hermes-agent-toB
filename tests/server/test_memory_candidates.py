from __future__ import annotations


def test_memory_candidate_lifecycle_is_user_scoped(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from server import memory

    monkeypatch.setattr(memory, "_db_path_cache", None)
    memory.init_db()

    candidate = memory.save_memory_candidate("u1", "s1", "remember this?", "User prefers CSV")
    memory.save_memory_candidate("u2", "s2", "private", "Other user fact")

    assert [c["id"] for c in memory.list_memory_candidates("u1")] == [candidate["id"]]

    approved = memory.approve_memory_candidate("u1", candidate["id"], content="User prefers CSV")

    assert approved is not None
    assert approved["content"] == "User prefers CSV"
    assert memory.list_memory_contents("u1") == ["User prefers CSV"]
    assert memory.list_memory_contents("u2") == []
    assert memory.approve_memory_candidate("u2", candidate["id"], content="bad") is None
