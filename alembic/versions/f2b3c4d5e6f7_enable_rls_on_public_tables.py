"""enable RLS on public tables

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PUBLIC_TABLES = (
    "alembic_version",
    "engagements",
    "leaves",
    "engagement_instances",
    "assignments",
    "email_outbox",
    "branches",
    "firm_users",
    "approval_rules",
    "approval_requests",
    "clients",
    "system_settings",
    "team_members",
    "firms",
    "license_inventory",
    "super_admins",
    "dev_portal_users",
    "users",
)


def upgrade() -> None:
    for table_name in PUBLIC_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table_name in reversed(PUBLIC_TABLES):
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')