"""Store the public avatar and optional trophy summary on the PSN profile."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e55ca287043a"
down_revision = "f079a525cc1c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psn_profiles", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column("psn_profiles", sa.Column("trophy_summary", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("psn_profiles", "trophy_summary")
    op.drop_column("psn_profiles", "avatar_url")
