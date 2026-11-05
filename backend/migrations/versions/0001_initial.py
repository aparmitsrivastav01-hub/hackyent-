"""Initial OpportunityOS schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("opportunities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("title", sa.String(300), nullable=False), sa.Column("normalized_title", sa.String(300), nullable=False), sa.Column("opportunity_type", sa.String(20), nullable=False), sa.Column("organization", sa.String(300)), sa.Column("normalized_organization", sa.String(300), nullable=False), sa.Column("description", sa.Text()), sa.Column("summary", sa.Text()), sa.Column("source_url", sa.Text(), nullable=False), sa.Column("application_url", sa.Text()), sa.Column("location", sa.String(300)), sa.Column("work_mode", sa.String(30)), sa.Column("deadline", sa.DateTime(timezone=True)), sa.Column("start_date", sa.DateTime(timezone=True)), sa.Column("end_date", sa.DateTime(timezone=True)), sa.Column("stipend", sa.String(200)), sa.Column("prize_pool", sa.String(200)), sa.Column("fees", sa.String(200)), sa.Column("eligibility", sa.Text()), sa.Column("required_skills", sa.JSON(), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("raw_data", sa.JSON(), nullable=False), sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("source_url", name="uq_opportunity_source_url"))
    for col in ["title", "normalized_title", "opportunity_type", "normalized_organization", "status"]: op.create_index(f"ix_opportunities_{col}", "opportunities", [col])
    op.create_table("opportunity_matches", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("overall_score", sa.Integer(), nullable=False), sa.Column("skill_score", sa.Integer(), nullable=False), sa.Column("eligibility_score", sa.Integer(), nullable=False), sa.Column("location_score", sa.Integer(), nullable=False), sa.Column("experience_score", sa.Integer(), nullable=False), sa.Column("explanation", sa.Text(), nullable=False))
    op.create_table("research_runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source", sa.String(100), nullable=False), sa.Column("opportunity_type", sa.String(20), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("records_found", sa.Integer(), nullable=False), sa.Column("records_saved", sa.Integer(), nullable=False), sa.Column("errors", sa.JSON(), nullable=False), sa.Column("raw_output", sa.JSON(), nullable=False))
    op.create_index("ix_research_runs_status", "research_runs", ["status"])
    op.create_table("user_profiles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("profile", sa.JSON(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("user_profiles"); op.drop_index("ix_research_runs_status", table_name="research_runs"); op.drop_table("research_runs"); op.drop_table("opportunity_matches")
    for col in ["title", "normalized_title", "opportunity_type", "normalized_organization", "status"]: op.drop_index(f"ix_opportunities_{col}", table_name="opportunities")
    op.drop_table("opportunities")
