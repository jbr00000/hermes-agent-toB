"""permission_leases.expires_at 允许 NULL（权限一次切换持久化）

原为 NOT NULL 的定时租约（前端写死 15 分钟 TTL，过期静默回落只读），
且内联执行路径在每次运行结束时主动吊销。改为：expires_at NULL 表示
持久权限——用户切到受控/完全访问后长期生效，直到手动切回只读；
非 NULL 仍为定时租约，兼容仍传 ttl_seconds 的旧客户端。
SQLite 用 batch 模式重建表；零配置 SQLite 新库由 models.py create_all
直接得到 nullable 列。

Revision ID: b5e9d2f47c31
Revises: e6b3d1f28a05
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e9d2f47c31"
down_revision: Union[str, Sequence[str], None] = "e6b3d1f28a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("permission_leases") as batch_op:
        batch_op.alter_column("expires_at", existing_type=sa.Double(), nullable=True)


def downgrade() -> None:
    # 回填持久租约为"创建即过期"，再恢复 NOT NULL
    op.execute(
        sa.text("UPDATE permission_leases SET expires_at = created_at WHERE expires_at IS NULL")
    )
    with op.batch_alter_table("permission_leases") as batch_op:
        batch_op.alter_column("expires_at", existing_type=sa.Double(), nullable=False)
