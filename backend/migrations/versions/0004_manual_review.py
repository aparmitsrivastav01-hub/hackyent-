"""Flag uncertain opportunities for manual review."""
from alembic import op
import sqlalchemy as sa

revision = "0004_manual_review"
down_revision = "0003_research_metrics"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("opportunities", sa.Column("review_status", sa.String(length=20), nullable=False, server_default="pending"))
    op.create_index("ix_opportunities_review_status", "opportunities", ["review_status"])

def downgrade():
    op.drop_index("ix_opportunities_review_status", table_name="opportunities")
    op.drop_column("opportunities", "review_status")
