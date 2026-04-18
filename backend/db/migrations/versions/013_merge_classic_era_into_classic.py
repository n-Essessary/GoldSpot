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
    # Step 1: Re-point server_aliases
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

    # Step 2: Rename non-colliding Classic Era → Classic in-place
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

    # Step 2.5: Delete snapshots referencing colliding Classic Era server_ids
    op.execute("""
        DELETE FROM price_snapshots
        WHERE server_id IN (
            SELECT id FROM servers WHERE version = 'Classic Era'
        )
    """)

    # Step 2.6: Same for other snapshot tables
    op.execute("""
        DELETE FROM server_price_index
        WHERE server_id IN (
            SELECT id FROM servers WHERE version = 'Classic Era'
        )
    """)

    op.execute("""
        DELETE FROM price_index_snapshots
        WHERE server_id IN (
            SELECT id FROM servers WHERE version = 'Classic Era'
        )
    """)

    # Tier index snapshots (012): FK to servers without CASCADE — must clear before DELETE servers
    for tbl in ("snapshots_1m", "snapshots_5m", "snapshots_1h", "snapshots_1d"):
        op.execute(f"""
            DELETE FROM {tbl}
            WHERE server_id IN (
                SELECT id FROM servers WHERE version = 'Classic Era'
            )
        """)

    # Step 3: Now safe to delete
    op.execute("""
        DELETE FROM servers WHERE version = 'Classic Era'
    """)


def downgrade():
    pass  # intentionally irreversible
