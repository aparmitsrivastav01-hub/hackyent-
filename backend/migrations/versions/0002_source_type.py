"""Store source provenance classification."""
from alembic import op
import sqlalchemy as sa

revision = "0002_source_type"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("opportunities", sa.Column("source_type", sa.String(length=20), nullable=False, server_default="unknown"))

def downgrade():
    op.drop_column("opportunities", "source_type")
