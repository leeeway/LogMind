"""add change event table

Revision ID: 20260702_01
Revises: 20260701_01
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_01"
down_revision = "20260701_01"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    if "change_event" in _table_names():
        return

    op.create_table(
        "change_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("service_name", sa.String(length=200), nullable=False),
        sa.Column("change_type", sa.String(length=30), nullable=False),
        sa.Column("version", sa.String(length=200), nullable=True),
        sa.Column("operator", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlated_spikes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_change_event_tenant_id", "change_event", ["tenant_id"])
    op.create_index("ix_change_event_service_name", "change_event", ["service_name"])
    op.create_index("ix_change_event_timestamp", "change_event", ["timestamp"])


def downgrade() -> None:
    if "change_event" not in _table_names():
        return

    op.drop_index("ix_change_event_timestamp", table_name="change_event")
    op.drop_index("ix_change_event_service_name", table_name="change_event")
    op.drop_index("ix_change_event_tenant_id", table_name="change_event")
    op.drop_table("change_event")
