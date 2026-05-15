"""Add alert_type and business_line_id to alert_history

Revision ID: 20260515_01
Revises: 20260511_01
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_01"
down_revision = "20260511_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_history",
        sa.Column("alert_type", sa.String(20), nullable=False, server_default="realtime"),
    )
    op.add_column(
        "alert_history",
        sa.Column("business_line_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_alert_history_business_line_id",
        "alert_history",
        ["business_line_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_history_business_line_id", table_name="alert_history")
    op.drop_column("alert_history", "business_line_id")
    op.drop_column("alert_history", "alert_type")
