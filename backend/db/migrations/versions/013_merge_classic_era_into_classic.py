"""merge classic era into classic

Revision ID: 013
Revises: 012
Create Date: 2026-04-18

Before upgrade, check for (name, region) pairs that have both versions:

    SELECT s.name, s.region FROM servers s
    JOIN servers e ON s.name = e.name AND s.region = e.region
    WHERE s.version = 'Classic Era' AND e.version = 'Classic';
"""
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    # Step 1: Re-point server_aliases from Classic Era server_id → Classic server_id
    # For each (name, region) pair that exists in both versions,
    # update aliases pointing to the Classic Era row to point to the Classic row instead.
    op.execute("""
        UPDATE server_aliases sa
        SET server_id = s_classic.id
        FROM servers s_era
        JOIN servers s_classic
          ON s_era.name = s_classic.name
         AND s_era.region = s_classic.region
         AND s_classic.version = 'Classic'
        WHERE sa.server_id = s_era.id
          AND s_era.version = 'Classic Era'
    """)

    # Step 2: For Classic Era servers with NO Classic counterpart (no collision),
    # just rename version in-place — no deletion needed.
    op.execute("""
        UPDATE servers
        SET version = 'Classic'
        WHERE version = 'Classic Era'
          AND NOT EXISTS (
              SELECT 1 FROM servers s2
              WHERE s2.name = servers.name
                AND s2.region = servers.region
                AND s2.version = 'Classic'
          )
    """)

    # Step 3: Delete Classic Era rows that now have a Classic counterpart.
    # price_snapshots/server_price_index will cascade-delete (acceptable).
    # server_aliases were already re-pointed in Step 1 — no data loss there.
    op.execute("""
        DELETE FROM servers
        WHERE version = 'Classic Era'
    """)


def downgrade():
    pass  # intentionally irreversible
