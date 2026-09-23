"""Add reviewable public form submissions.

Revision ID: c9e2f6a1b4d8
Revises: 6d92a7f4c1e8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c9e2f6a1b4d8"
down_revision = "6d92a7f4c1e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_form_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("form_id", sa.Integer(), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("reference", sa.String(32), nullable=False),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_note", sa.String(500), nullable=True),
        sa.Column("student_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_public_form_submission_status"),
        sa.ForeignKeyConstraint(["form_id"], ["public_forms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
        sa.UniqueConstraint("student_id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_public_form_submissions_school_status_created", "public_form_submissions", ["school_id", "status", "created_at"])
    op.execute("ALTER TABLE public_form_submissions ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_public_form_submissions_school_status_created", table_name="public_form_submissions")
    op.drop_table("public_form_submissions")
