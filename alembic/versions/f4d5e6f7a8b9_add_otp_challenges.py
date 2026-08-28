"""add durable OTP challenges

Revision ID: f4d5e6f7a8b9
Revises: f3c4d5e6f7a8
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "f3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("otp_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hourly_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hourly_reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_otp_challenges_email", "otp_challenges", ["email"])
    op.execute('ALTER TABLE "otp_challenges" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute('ALTER TABLE "otp_challenges" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_otp_challenges_email", table_name="otp_challenges")
    op.drop_table("otp_challenges")