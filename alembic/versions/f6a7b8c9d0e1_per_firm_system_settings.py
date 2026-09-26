"""allow per-firm system settings

system_settings.key was globally unique, so a firm could not hold its own value
for a key and firm-level changes overwrote the platform default for every firm.
Replace it with one unique default per key (firm_id IS NULL) and one unique
override per (firm_id, key). firm_id now cascades on firm deletion so orphaned
overrides cannot collide with the defaults.

Revision ID: f6a7b8c9d0e1
Revises: f5e6f7a8b9c0
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "f5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_key_key")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_default_key "
        "ON system_settings (key) WHERE firm_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_firm_key "
        "ON system_settings (firm_id, key) WHERE firm_id IS NOT NULL"
    )
    op.execute("ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_firm_id_fkey")
    op.execute(
        "ALTER TABLE system_settings ADD CONSTRAINT system_settings_firm_id_fkey "
        "FOREIGN KEY (firm_id) REFERENCES firms (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    # Per-firm overrides cannot be represented under a globally unique key; drop them.
    op.execute("DELETE FROM system_settings WHERE firm_id IS NOT NULL")
    op.execute("ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_firm_id_fkey")
    op.execute(
        "ALTER TABLE system_settings ADD CONSTRAINT system_settings_firm_id_fkey "
        "FOREIGN KEY (firm_id) REFERENCES firms (id) ON DELETE SET NULL"
    )
    op.execute("DROP INDEX IF EXISTS uq_system_settings_firm_key")
    op.execute("DROP INDEX IF EXISTS uq_system_settings_default_key")
    op.execute("ALTER TABLE system_settings ADD CONSTRAINT system_settings_key_key UNIQUE (key)")
