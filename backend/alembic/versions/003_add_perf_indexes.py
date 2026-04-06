"""Add indexes for price snapshot / history queries and LOWER(alias) batch lookups.

Revision ID: 003
Revises: 002
Create Date: 2026-04-06
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_snapshots_server_faction
            ON price_snapshots(server_id, faction, fetched_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_server_price_history_lookup
            ON server_price_history(server_id, faction, recorded_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_server_aliases_lower
            ON server_aliases(LOWER(alias));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_server_aliases_lower;")
    op.execute("DROP INDEX IF EXISTS idx_server_price_history_lookup;")
    op.execute("DROP INDEX IF EXISTS idx_price_snapshots_server_faction;")
