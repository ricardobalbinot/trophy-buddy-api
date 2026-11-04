"""Give product users their own identity without claiming ownership of queried PSN profiles."""

import sqlalchemy as sa
from alembic import op

revision = "b84e03fa721c"
down_revision = "a73d92ef610b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("uid", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=True),
    )
    # Existing library owners are already verified Firebase identities; PSN IDs are not users.
    op.execute("INSERT INTO users (uid) SELECT uid FROM libraries")
    op.create_foreign_key(
        "fk_libraries_user", "libraries", "users", ["uid"], ["uid"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_libraries_user", "libraries", type_="foreignkey")
    op.drop_table("users")
