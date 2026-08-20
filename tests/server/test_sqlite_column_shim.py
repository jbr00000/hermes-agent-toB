"""The SQLite zero-config path must backfill columns added after first ship.

``create_all`` never alters existing tables, so ``init_database()`` carries a
small table-driven shim; MySQL deployments use Alembic instead.
"""
from __future__ import annotations

import json
import time

from sqlalchemy import create_engine, text


def _build_legacy_db(tmp_path) -> str:
    """Create a users table the way it looked before the features column."""
    db_path = tmp_path / "hermes_home" / "hermes_tob.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+pysqlite:///{db_path.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    username VARCHAR(128) NOT NULL,
                    password_hash VARCHAR(128) NOT NULL,
                    role VARCHAR(16) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    created_at DOUBLE NOT NULL,
                    updated_at DOUBLE NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, password_hash, role,"
                " status, created_at, updated_at)"
                " VALUES ('u1', 'default', 'legacy', 'hash', 'admin', 'active', :t, :t)"
            ),
            {"t": time.time()},
        )
    engine.dispose()
    return url


def test_sqlite_shim_adds_and_backfills_features(monkeypatch, tmp_path) -> None:
    url = _build_legacy_db(tmp_path)
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()

    engine = create_engine(url)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        assert "features" in cols
        stored = conn.execute(
            text("SELECT features FROM users WHERE id = 'u1'")
        ).scalar()
    assert stored is not None
    assert json.loads(stored) == {
        "agent": True,
        "chat": True,
        "knowledge": True,
        "memory": True,
    }

    # Reading through the repository normalizes to the all-enabled default.
    from server.storage import get_repository, reset_storage_for_tests as reset_again

    reset_again()
    user = get_repository().get_user("u1")
    assert user["features"] == {
        "agent": True,
        "chat": True,
        "knowledge": True,
        "memory": True,
    }
    engine.dispose()


def test_sqlite_shim_adds_must_change_password(monkeypatch, tmp_path) -> None:
    url = _build_legacy_db(tmp_path)
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()

    engine = create_engine(url)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        assert "must_change_password" in cols
        # 存量行默认 0：老用户不被强制改密。
        stored = conn.execute(
            text("SELECT must_change_password FROM users WHERE id = 'u1'")
        ).scalar()
    assert stored == 0
    engine.dispose()


def test_sqlite_shim_is_idempotent(monkeypatch, tmp_path) -> None:
    url = _build_legacy_db(tmp_path)
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()
    reset_storage_for_tests()
    init_database()  # second run must be a no-op, not an error

    engine = create_engine(url)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    assert count == 1
    engine.dispose()


def test_sqlite_shim_promotes_oldest_admin_to_superadmin(monkeypatch, tmp_path) -> None:
    """升级路径：无 active superadmin 时把最老的 active admin 提为 superadmin，
    幂等（对应 MySQL 侧 3b7e1f9a42c6 的 data migration）。"""
    url = _build_legacy_db(tmp_path)  # u1 = 最老的 active admin
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, password_hash, role,"
                " status, created_at, updated_at)"
                " VALUES ('u2', 'default', 'newer-admin', 'hash', 'admin', 'active', :t, :t)"
            ),
            {"t": time.time() + 60},
        )
    engine.dispose()
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()

    engine = create_engine(url)
    with engine.connect() as conn:
        roles = dict(
            conn.execute(text("SELECT id, role FROM users")).fetchall()
        )
    assert roles["u1"] == "superadmin"
    assert roles["u2"] == "admin"  # 只提升最老的一个
    engine.dispose()

    # 幂等：二次 init 不再提升 u2
    reset_storage_for_tests()
    init_database()
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'superadmin'")
        ).scalar() == 1
    engine.dispose()


def test_sqlite_shim_leaves_existing_superadmin_untouched(monkeypatch, tmp_path) -> None:
    """已有 superadmin 的库不做任何提升。"""
    url = _build_legacy_db(tmp_path)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'superadmin' WHERE id = 'u1'")
        )
    engine.dispose()
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()

    engine = create_engine(url)
    with engine.connect() as conn:
        roles = dict(conn.execute(text("SELECT id, role FROM users")).fetchall())
    assert roles == {"u1": "superadmin"}
    engine.dispose()


def _build_legacy_knowledge_db(tmp_path) -> str:
    """Knowledge tables as they looked before kb_id / knowledge_bases existed."""
    db_path = tmp_path / "hermes_home" / "hermes_tob.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+pysqlite:///{db_path.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE knowledge_documents (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    uploader_id VARCHAR(36) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    file_ext VARCHAR(16) NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    error TEXT,
                    parser VARCHAR(16),
                    chunk_count INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    created_at DOUBLE NOT NULL,
                    updated_at DOUBLE NOT NULL,
                    finished_at DOUBLE
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE knowledge_chunks (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    doc_id VARCHAR(36) NOT NULL,
                    doc_name VARCHAR(255) NOT NULL,
                    chunk_title VARCHAR(512),
                    content TEXT NOT NULL,
                    doc_pos INTEGER NOT NULL,
                    token_num INTEGER NOT NULL,
                    is_use BOOLEAN NOT NULL,
                    created_at DOUBLE NOT NULL
                )
                """
            )
        )
        now = time.time()
        conn.execute(
            text(
                "INSERT INTO knowledge_documents (id, tenant_id, uploader_id, title,"
                " file_name, file_ext, size_bytes, file_path, status, chunk_count,"
                " retry_count, created_at, updated_at)"
                " VALUES ('doc-1', 'default', 'u1', '规范', '规范.pdf', '.pdf', 10,"
                " 'knowledge/files/a.pdf', 'ready', 1, 0, :t, :t)"
            ),
            {"t": now},
        )
        conn.execute(
            text(
                "INSERT INTO knowledge_chunks (id, tenant_id, doc_id, doc_name, content,"
                " doc_pos, token_num, is_use, created_at)"
                " VALUES ('c-1', 'default', 'doc-1', '规范.pdf', '内容', 0, 5, 1, :t)"
            ),
            {"t": now},
        )
    engine.dispose()
    return url


def test_sqlite_shim_backfills_kb_id_into_default_base(monkeypatch, tmp_path) -> None:
    """存量文档/分块回填 kb_id 并挂进自动创建的默认知识库（含计数）。"""
    url = _build_legacy_knowledge_db(tmp_path)
    monkeypatch.setenv("HERMES_DATABASE_URL", url)

    from server.storage import reset_storage_for_tests

    reset_storage_for_tests()

    from server.storage.database import init_database

    init_database()

    engine = create_engine(url)
    with engine.connect() as conn:
        base = conn.execute(
            text("SELECT id, name, doc_count, chunk_count FROM knowledge_bases")
        ).one()
        doc_kb = conn.execute(
            text("SELECT kb_id FROM knowledge_documents WHERE id = 'doc-1'")
        ).scalar()
        chunk_kb = conn.execute(
            text("SELECT kb_id FROM knowledge_chunks WHERE id = 'c-1'")
        ).scalar()
    assert doc_kb == base[0]
    assert chunk_kb == base[0]
    assert base[1]  # 默认库名非空（具体文案可变）
    assert base[2] == 1  # doc_count
    assert base[3] == 1  # chunk_count

    # 幂等：二次 init 不重复建库
    from server.storage import reset_storage_for_tests as reset_again

    reset_again()
    init_database()
    engine2 = create_engine(url)
    with engine2.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM knowledge_bases")).scalar() == 1
    engine.dispose()
    engine2.dispose()
