"""Persist role and event detail fields."""
from alembic import op
import sqlalchemy as sa

revision = "0005_opportunity_details"
down_revision = "0004_manual_review"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("opportunities", sa.Column("themes", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("opportunities", sa.Column("team_size", sa.String(length=100)))
    op.add_column("opportunities", sa.Column("experience_requirements", sa.Text()))
    op.add_column("opportunities", sa.Column("duration", sa.String(length=200)))
    op.add_column("opportunities", sa.Column("employment_type", sa.String(length=100)))

def downgrade():
    for column in ("employment_type", "duration", "experience_requirements", "team_size", "themes"):
        op.drop_column("opportunities", column)
