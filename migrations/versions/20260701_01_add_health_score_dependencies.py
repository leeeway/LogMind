"""add health score dependency columns

Revision ID: 20260701_01
Revises: 20260515_01
Create Date: 2026-07-01

Adds columns used by health scoring and priority scoring. The migration is
idempotent so databases that previously ran the legacy standalone migration
scripts can still move onto the Alembic version chain.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260701_01"
down_revision = "20260515_01"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "business_line",
        sa.Column("business_weight", sa.Integer(), nullable=False, server_default="5"),
    )
    _add_column_if_missing(
        "business_line",
        sa.Column("is_core_path", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column_if_missing(
        "business_line",
        sa.Column("estimated_dau", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "business_line",
        sa.Column("night_policy", sa.String(20), nullable=False, server_default="p0_only"),
    )
    _add_column_if_missing(
        "business_line",
        sa.Column("night_hours", sa.String(20), nullable=False, server_default="22:00-08:00"),
    )
    _add_column_if_missing(
        "business_line",
        sa.Column("auto_remediation_config", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "analysis_result",
        sa.Column("feedback_score", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        "analysis_result",
        sa.Column("feedback_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for table_name, column_name in [
        ("analysis_result", "feedback_comment"),
        ("analysis_result", "feedback_score"),
        ("business_line", "auto_remediation_config"),
        ("business_line", "night_hours"),
        ("business_line", "night_policy"),
        ("business_line", "estimated_dau"),
        ("business_line", "is_core_path"),
        ("business_line", "business_weight"),
    ]:
        if column_name in _column_names(table_name):
            op.drop_column(table_name, column_name)
