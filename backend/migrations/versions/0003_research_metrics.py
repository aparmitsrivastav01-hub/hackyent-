"""Add source and duplicate counters to research runs."""
from alembic import op
import sqlalchemy as sa

revision = "0003_research_metrics"
down_revision = "0002_source_type"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("research_runs", sa.Column("duplicates_detected", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("research_runs", sa.Column("sources_searched", sa.JSON(), nullable=False, server_default="[]"))

def downgrade():
    op.drop_column("research_runs", "sources_searched")
    op.drop_column("research_runs", "duplicates_detected")
