"""Store provider activity dates instead of imported list positions."""

import sqlalchemy as sa
from alembic import op

revision = "a73d92ef610b"
down_revision = "45bd62df4809"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing positions cannot recover historical dates; the next sync fills these columns.
    op.add_column("library_games", sa.Column("last_played_at", sa.DateTime(timezone=True)))
    op.add_column(
        "library_games", sa.Column("trophy_progress_updated_at", sa.DateTime(timezone=True))
    )
    op.drop_column("library_games", "position")


def downgrade() -> None:
    # Restore a deterministic position without pretending to recover Sony's original order.
    op.add_column("library_games", sa.Column("position", sa.Integer(), nullable=True))
    op.execute(
        "WITH ranked AS ("
        "SELECT uid, game_id, row_number() OVER (PARTITION BY uid "
        "ORDER BY last_played_at DESC NULLS LAST, game_id) - 1 AS position FROM library_games"
        ") UPDATE library_games AS game SET position = ranked.position FROM ranked "
        "WHERE game.uid = ranked.uid AND game.game_id = ranked.game_id"
    )
    op.alter_column("library_games", "position", nullable=False)
    op.drop_column("library_games", "trophy_progress_updated_at")
    op.drop_column("library_games", "last_played_at")
