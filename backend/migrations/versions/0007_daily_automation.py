"""Persist dashboard control for the external daily automation."""
from alembic import op
import sqlalchemy as sa

revision = "0007_daily_automation"
down_revision = "0006_email_deliveries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("opportunities", sa.Column("notification_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("email_delivery_items", sa.Column("opportunity_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("automation_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("automation_settings")
    op.drop_column("email_delivery_items", "opportunity_version")
    op.drop_column("opportunities", "notification_version")
