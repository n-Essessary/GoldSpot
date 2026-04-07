"""Add (source, offer_id, fetched_at DESC) index to price_snapshots for
efficient per-offer history queries and upsert support.

Also adds a non-unique index on (source, offer_id) alone for deduplication
lookups — enables O(log n) per-offer access patterns used by the normalize
pipeline's in-memory dedup and future admin tooling.

Revision ID: 005
Revises: 004
Create Date: 2026-04-07
"""
from alembic import op

revision     = "005"
down_revision = "004"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── Composite index for per-offer history access ──────────────────────────
    # Enables queries like:
    #   SELECT * FROM price_snapshots
    #   WHERE source = $1 AND offer_id = $2
    #   ORDER BY fetched_at DESC LIMIT 1;
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_psnap_source_offer_fetched
            ON price_snapshots (source, offer_id, fetched_at DESC);
    """)

    # ── Plain (source, offer_id) index for dedup / count queries ─────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_psnap_source_offer
            ON price_snapshots (source, offer_id);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_psnap_source_offer_fetched;")
    op.execute("DROP INDEX IF EXISTS idx_psnap_source_offer;")
