"""add HTTP access site governance

Revision ID: 20260810_01
Revises: 20260702_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260810_01"
down_revision = "20260702_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "http_access_site_config" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "http_access_site_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("site", sa.String(253), nullable=False),
        sa.Column("sources", sa.Text(), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("monitoring_mode", sa.String(16), nullable=False),
        sa.Column("enable_4xx", sa.Boolean(), nullable=False),
        sa.Column("enable_latency", sa.Boolean(), nullable=False),
        sa.Column("enable_traffic_drop", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(24), nullable=False),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "site", name="uq_http_access_site_tenant_site"),
    )
    op.create_index("ix_http_access_site_config_tenant_id", "http_access_site_config", ["tenant_id"])
    op.create_index("ix_http_access_site_tenant_mode", "http_access_site_config", ["tenant_id", "monitoring_mode"])


def downgrade() -> None:
    op.drop_table("http_access_site_config")
