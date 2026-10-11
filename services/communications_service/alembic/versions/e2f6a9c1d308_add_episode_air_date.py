"""Keep podcast broadcast date distinct from content publication/upload date."""

from alembic import op
import sqlalchemy as sa

revision = "e2f6a9c1d308"
down_revision = "913ae70b4c21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "content_posts", sa.Column("episode_air_date", sa.Date(), nullable=True)
    )


def downgrade():
    op.drop_column("content_posts", "episode_air_date")
