"""init applications, approval records, sync outbox, room locks

Revision ID: 0001_init
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("room_id", sa.String(length=32), nullable=False),
        sa.Column("room_name", sa.String(length=128), nullable=False),
        sa.Column("applicant_id", sa.String(length=64), nullable=False),
        sa.Column("applicant_name", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reject_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_applications_applicant_id", "applications", ["applicant_id"])
    op.create_index("ix_applications_status", "applications", ["status"])
    op.create_index("ix_applications_room_time", "applications", ["room_id", "start_time", "end_time"])
    op.create_index("ix_applications_status_expires", "applications", ["status", "expires_at"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.String(length=64), nullable=False),
        sa.Column("approver_name", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_records_application_id", "approval_records", ["application_id"])

    op.create_table(
        "sync_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "action", name="uq_sync_application_action"),
    )
    op.create_index("ix_sync_records_application_id", "sync_records", ["application_id"])
    op.create_index("ix_sync_records_status", "sync_records", ["status"])

    op.create_table(
        "room_locks",
        sa.Column("room_id", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("room_id"),
    )


def downgrade() -> None:
    op.drop_table("room_locks")
    op.drop_index("ix_sync_records_status", table_name="sync_records")
    op.drop_index("ix_sync_records_application_id", table_name="sync_records")
    op.drop_table("sync_records")
    op.drop_index("ix_approval_records_application_id", table_name="approval_records")
    op.drop_table("approval_records")
    op.drop_index("ix_applications_status_expires", table_name="applications")
    op.drop_index("ix_applications_room_time", table_name="applications")
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_applicant_id", table_name="applications")
    op.drop_table("applications")
