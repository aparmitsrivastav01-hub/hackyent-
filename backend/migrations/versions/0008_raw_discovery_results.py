"""Persist TinyFish pages independently of Gemini extraction."""
from alembic import op
import sqlalchemy as sa

revision = "0008_raw_discovery_results"
down_revision = "0007_daily_automation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("email_deliveries", sa.Column("raw_result_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "raw_discovery_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_type", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("source_title", sa.String(length=500), nullable=False, server_default="Untitled source"),
        sa.Column("source_name", sa.String(length=300), nullable=False, server_default="Unknown source"),
        sa.Column("source_url", sa.Text(), nullable=False, unique=True),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_status", sa.String(length=30), nullable=False, server_default="PENDING_EXTRACTION"),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emailed_at", sa.DateTime(timezone=True)),
        sa.Column("email_delivery_id", sa.Integer(), sa.ForeignKey("email_deliveries.id", ondelete="SET NULL")),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id", ondelete="SET NULL")),
    )
    for column in ("run_id", "opportunity_type", "extraction_status"):
        op.create_index(f"ix_raw_discovery_results_{column}", "raw_discovery_results", [column])


def downgrade():
    for column in ("extraction_status", "opportunity_type", "run_id"):
        op.drop_index(f"ix_raw_discovery_results_{column}", table_name="raw_discovery_results")
    op.drop_table("raw_discovery_results")
    op.drop_column("email_deliveries", "raw_result_count")
