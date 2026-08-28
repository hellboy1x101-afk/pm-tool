"""add firm license key

Revision ID: f3c4d5e6f7a8
Revises: f2b3c4d5e6f7
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "f2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE firms ADD COLUMN IF NOT EXISTS license_key VARCHAR(255)"
    )


def downgrade() -> None:
    op.drop_column("firms", "license_key")