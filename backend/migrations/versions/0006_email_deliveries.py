"""Track email digest attempts and the opportunities each email contains."""
from alembic import op
import sqlalchemy as sa

revision = "0006_email_deliveries"
down_revision = "0005_opportunity_details"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="resend"),
        sa.Column("provider_message_id", sa.String(length=300)),
        sa.Column("error", sa.Text()),
        sa.Column("opportunity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_deliveries_status", "email_deliveries", ["status"])
    op.create_index("ix_email_deliveries_created_at", "email_deliveries", ["created_at"])
    op.create_table(
        "email_delivery_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("email_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("delivery_id", "opportunity_id", name="uq_email_delivery_opportunity"),
    )
    op.create_index("ix_email_delivery_items_delivery_id", "email_delivery_items", ["delivery_id"])
    op.create_index("ix_email_delivery_items_opportunity_id", "email_delivery_items", ["opportunity_id"])


def downgrade():
    op.drop_index("ix_email_delivery_items_opportunity_id", table_name="email_delivery_items")
    op.drop_index("ix_email_delivery_items_delivery_id", table_name="email_delivery_items")
    op.drop_table("email_delivery_items")
    op.drop_index("ix_email_deliveries_created_at", table_name="email_deliveries")
    op.drop_index("ix_email_deliveries_status", table_name="email_deliveries")
    op.drop_table("email_deliveries")
