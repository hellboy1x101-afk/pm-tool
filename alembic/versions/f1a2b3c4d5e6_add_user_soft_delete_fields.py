"""add user soft delete fields

Revision ID: f1a2b3c4d5e6
Revises: e4f5a6b7c8d9
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_by_user_id INTEGER"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_users_deleted_by_user_id'
            ) THEN
                ALTER TABLE users
                ADD CONSTRAINT fk_users_deleted_by_user_id
                FOREIGN KEY (deleted_by_user_id) REFERENCES users(id)
                ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_deleted_by_user_id", "users", type_="foreignkey")
    op.drop_column("users", "deleted_by_user_id")
    op.drop_column("users", "deleted_at")