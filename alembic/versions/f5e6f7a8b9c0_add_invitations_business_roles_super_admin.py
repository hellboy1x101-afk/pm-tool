"""add invitations, firm_business_roles and technicalrole.super_admin

The Invitation and FirmBusinessRole models and the TechnicalRole.super_admin
value existed in the ORM with no migration, so a database built from
migrations could not create or list invitations, or store cost rates.

Idempotent so it also applies to databases where these were created by hand.

Revision ID: f5e6f7a8b9c0
Revises: f4d5e6f7a8b9
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "f4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE cannot be used in the same transaction that adds it, so run it on its own.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE technicalrole ADD VALUE IF NOT EXISTS 'super_admin'")

    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if "invitations" not in existing:
        op.create_table(
            "invitations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("firm_id", sa.Integer(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=50), nullable=False, server_default="viewer"),
            sa.Column("invited_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("token", sa.String(length=255), nullable=False),
            sa.Column("is_used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token", name="uq_invitations_token"),
        )
        op.create_index("ix_invitations_id", "invitations", ["id"])
        op.create_index("ix_invitations_firm_id_email", "invitations", ["firm_id", "email"])

    if "firm_business_roles" not in existing:
        op.create_table(
            "firm_business_roles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("firm_id", sa.Integer(), sa.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role_code", sa.String(length=50), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("rate_type", sa.String(length=10), nullable=True),
            sa.Column("rate_value", sa.Numeric(10, 2), nullable=True),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("firm_id", "role_code", name="uq_firm_business_role"),
        )
        op.create_index("ix_firm_business_roles_id", "firm_business_roles", ["id"])

    for table_name in ("invitations", "firm_business_roles"):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("firm_business_roles")
    op.drop_table("invitations")
    # Postgres cannot drop a single enum value; technicalrole keeps 'super_admin'.
