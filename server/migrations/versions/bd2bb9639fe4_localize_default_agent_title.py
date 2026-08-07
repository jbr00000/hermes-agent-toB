"""localize legacy default Agent task titles

Revision ID: bd2bb9639fe4
Revises: aa53f3f7810b
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bd2bb9639fe4"
down_revision: Union[str, Sequence[str], None] = "aa53f3f7810b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE agent_tasks SET title = :new WHERE title = :old").bindparams(
            new="新任务", old="New agent task"
        )
    )
    op.execute(
        sa.text("UPDATE conversations SET title = :new WHERE title = :old").bindparams(
            new="新任务", old="New agent task"
        )
    )


def downgrade() -> None:
    # The localized value may also be a user-provided title, so reversing this
    # data migration would risk changing user data.
    pass
